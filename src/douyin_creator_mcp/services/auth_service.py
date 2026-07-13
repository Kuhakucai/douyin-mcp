"""OAuth flow and authorization status service."""

from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from ..config import Settings, validate_for_auth
from ..errors import VALIDATION_ERROR, AppError
from ..storage.db import Database
from ..storage.token_store import TokenStore
from .account_service import AccountService


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AuthService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        api_client: Any,
        token_store: TokenStore,
        account_service: AccountService,
    ):
        self.settings = settings
        self.db = db
        self.api_client = api_client
        self.token_store = token_store
        self.account_service = account_service

    def start_auth(self, scopes: list[str] | None = None) -> dict[str, Any]:
        validate_for_auth(self.settings)
        requested_scopes = scopes or list(self.settings.douyin_scopes)
        state = secrets.token_urlsafe(32)
        auth_session_id = str(uuid.uuid4())
        created_at = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO oauth_states (state, auth_session_id, redirect_uri, scopes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                state,
                auth_session_id,
                self.settings.douyin_redirect_uri,
                json.dumps(requested_scopes, ensure_ascii=False),
                "pending",
                created_at,
            ),
        )
        query = urlencode(
            {
                "client_key": self.settings.douyin_client_key,
                "response_type": "code",
                "scope": ",".join(requested_scopes),
                "redirect_uri": self.settings.douyin_redirect_uri,
                "state": state,
            }
        )
        return {
            "authorization_url": f"{self.settings.douyin_base_url.rstrip('/')}/platform/oauth/connect/?{query}",
            "auth_session_id": auth_session_id,
            "state": state,
            "scopes": requested_scopes,
            "oauth_mode": self.settings.douyin_oauth_mode,
            "instructions": "Open authorization_url in a browser. In local_manual_code mode, paste the callback code into douyin_auth_complete.",
        }

    def _pending_state(self, state: str | None) -> dict[str, Any]:
        if state:
            row = self.db.query_one("SELECT * FROM oauth_states WHERE state = ?", (state,))
        else:
            row = self.db.query_one(
                """
                SELECT * FROM oauth_states
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        if not row:
            raise AppError(VALIDATION_ERROR, "OAuth state was not found or has expired.")
        if row["status"] != "pending":
            raise AppError(VALIDATION_ERROR, "OAuth state has already been consumed.")
        return row

    @staticmethod
    def _scopes_from_token(token_data: dict[str, Any], fallback_json: str) -> list[str]:
        raw = token_data.get("scope")
        if isinstance(raw, str) and raw:
            return [scope.strip() for scope in raw.replace(";", ",").split(",") if scope.strip()]
        if isinstance(raw, list):
            return [str(scope) for scope in raw]
        try:
            fallback = json.loads(fallback_json)
        except json.JSONDecodeError:
            return []
        return fallback if isinstance(fallback, list) else []

    def _complete_with_code(self, code: str, state: str | None) -> dict[str, Any]:
        state_row = self._pending_state(state)
        token_data = self.api_client.exchange_code_for_token(code)
        open_id = token_data.get("open_id")
        if not open_id:
            raise AppError("invalid_response", "Token response does not contain open_id.")
        scopes = self._scopes_from_token(token_data, state_row["scopes"])
        account = self.account_service.upsert_account(
            {
                "id": open_id,
                "open_id": open_id,
                "nickname": token_data.get("nickname"),
                "avatar": token_data.get("avatar"),
            },
            scopes,
        )
        now = int(time.time())
        self.token_store.save_tokens(
            account["id"],
            str(token_data["access_token"]),
            str(token_data["refresh_token"]),
            int(token_data.get("expires_at") or now + int(token_data.get("expires_in", 0))),
            token_data.get("refresh_expires_at"),
        )
        self.db.execute(
            """
            UPDATE oauth_states
            SET status = 'completed', consumed_at = ?, account_id = ?
            WHERE state = ?
            """,
            (utc_now_iso(), account["id"], state_row["state"]),
        )
        return {
            "auth_session_id": state_row["auth_session_id"],
            "account": {
                "id": account["id"],
                "nickname": account.get("nickname"),
            },
            "scopes": scopes,
        }

    def complete_auth(self, code: str, state: str | None = None) -> dict[str, Any]:
        if self.settings.douyin_oauth_mode != "local_manual_code":
            raise AppError(
                VALIDATION_ERROR,
                "douyin_auth_complete is only enabled in local_manual_code mode. Use HTTPS callback mode instead.",
                retryable=False,
            )
        return self._complete_with_code(code, state)

    def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        return self._complete_with_code(code, state)

    def get_auth_status(self, auth_session_id: str | None = None) -> dict[str, Any]:
        session = None
        if auth_session_id:
            session = self.db.query_one("SELECT * FROM oauth_states WHERE auth_session_id = ?", (auth_session_id,))
            if not session:
                raise AppError(VALIDATION_ERROR, "auth_session_id was not found.")
            session = {
                "auth_session_id": session["auth_session_id"],
                "status": session["status"],
                "created_at": session["created_at"],
                "consumed_at": session["consumed_at"],
                "account_id": session["account_id"],
            }
        accounts = self.account_service.list_accounts()
        return {
            "auth_session": session,
            "accounts": [
                {
                    "id": account["id"],
                    "nickname": account.get("nickname"),
                    "authorized_scopes": account.get("authorized_scopes", []),
                }
                for account in accounts
            ],
            "requires_authorization": not accounts,
        }

    def refresh_token(self, account_id: str) -> dict[str, Any]:
        record = self.token_store.get_tokens(account_id)
        token_data = self.api_client.refresh_access_token(record.refresh_token)
        now = int(time.time())
        self.token_store.save_tokens(
            account_id,
            str(token_data["access_token"]),
            str(token_data.get("refresh_token") or record.refresh_token),
            int(token_data.get("expires_at") or now + int(token_data.get("expires_in", 0))),
            token_data.get("refresh_expires_at", record.refresh_expires_at),
        )
        return {"account_id": account_id, "status": "refreshed"}
