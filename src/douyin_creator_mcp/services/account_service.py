"""Account cache and profile service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..errors import AUTHORIZATION_REQUIRED, AppError
from ..storage.db import Database


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_scopes(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return [scope for scope in value.replace(";", ",").split(",") if scope]
    return payload if isinstance(payload, list) else []


class AccountService:
    def __init__(self, db: Database, api_client: Any | None = None):
        self.db = db
        self.api_client = api_client

    def upsert_account(self, profile: dict[str, Any], scopes: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        open_id = str(profile.get("open_id") or profile.get("id") or "")
        if not open_id:
            raise AppError("invalid_response", "Account profile does not contain open_id.")
        account_id = str(profile.get("id") or open_id)
        now = utc_now_iso()
        scopes_json = json.dumps(list(scopes or profile.get("authorized_scopes") or []), ensure_ascii=False)
        self.db.execute(
            """
            INSERT INTO accounts (id, open_id, nickname, avatar, authorized_scopes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              open_id=excluded.open_id,
              nickname=excluded.nickname,
              avatar=excluded.avatar,
              authorized_scopes=excluded.authorized_scopes,
              updated_at=excluded.updated_at
            """,
            (
                account_id,
                open_id,
                profile.get("nickname"),
                profile.get("avatar"),
                scopes_json,
                now,
                now,
            ),
        )
        return {
            "id": account_id,
            "open_id": open_id,
            "nickname": profile.get("nickname"),
            "avatar": profile.get("avatar"),
            "authorized_scopes": list(scopes or profile.get("authorized_scopes") or []),
        }

    def list_accounts(self) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT id, open_id, nickname, avatar, authorized_scopes, created_at, updated_at FROM accounts ORDER BY updated_at DESC"
        )
        for row in rows:
            row["authorized_scopes"] = parse_scopes(row.get("authorized_scopes"))
        return rows

    def get_account(self, account_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise AppError(
                AUTHORIZATION_REQUIRED,
                "Account is not authorized. Call douyin_auth_start first.",
                retryable=True,
            )
        row["authorized_scopes"] = parse_scopes(row.get("authorized_scopes"))
        return row

    def get_account_profile(self, account_id: str) -> dict[str, Any]:
        if self.api_client is None:
            return self.get_account(account_id)
        profile = self.api_client.get_user_info(account_id)
        existing = self.get_account(account_id)
        return self.upsert_account(profile, existing.get("authorized_scopes"))
