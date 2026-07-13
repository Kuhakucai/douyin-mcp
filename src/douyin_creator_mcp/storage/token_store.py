"""Encrypted token storage."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from cryptography.fernet import Fernet

from ..config import normalize_fernet_key
from ..errors import AUTHORIZATION_EXPIRED, AUTHORIZATION_REQUIRED, AppError
from .db import Database


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class TokenRecord:
    account_id: str
    access_token: str
    refresh_token: str
    expires_at: int
    refresh_expires_at: int | None


class TokenStore:
    def __init__(self, db: Database, encryption_key: str):
        self.db = db
        self.fernet = Fernet(normalize_fernet_key(encryption_key))

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    def save_tokens(
        self,
        account_id: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        refresh_expires_at: int | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO tokens (
              account_id,
              access_token_encrypted,
              refresh_token_encrypted,
              expires_at,
              refresh_expires_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
              access_token_encrypted=excluded.access_token_encrypted,
              refresh_token_encrypted=excluded.refresh_token_encrypted,
              expires_at=excluded.expires_at,
              refresh_expires_at=excluded.refresh_expires_at,
              updated_at=excluded.updated_at
            """,
            (
                account_id,
                self._encrypt(access_token),
                self._encrypt(refresh_token),
                int(expires_at),
                refresh_expires_at,
                utc_now_iso(),
            ),
        )

    def get_tokens(self, account_id: str) -> TokenRecord:
        row = self.db.query_one("SELECT * FROM tokens WHERE account_id = ?", (account_id,))
        if not row:
            raise AppError(
                AUTHORIZATION_REQUIRED,
                "Account is not authorized. Call douyin_auth_start first.",
                retryable=True,
            )
        return TokenRecord(
            account_id=account_id,
            access_token=self._decrypt(row["access_token_encrypted"]),
            refresh_token=self._decrypt(row["refresh_token_encrypted"]),
            expires_at=int(row["expires_at"]),
            refresh_expires_at=row["refresh_expires_at"],
        )

    def get_valid_token(
        self,
        account_id: str,
        refresh_callback: Callable[[str], dict[str, Any]] | None = None,
        leeway_seconds: int = 60,
    ) -> TokenRecord:
        record = self.get_tokens(account_id)
        now = int(time.time())
        if record.expires_at > now + leeway_seconds:
            return record
        if record.refresh_expires_at and int(record.refresh_expires_at) <= now:
            raise AppError(
                AUTHORIZATION_EXPIRED,
                "Refresh token is expired. Please authorize again.",
                retryable=True,
            )
        if refresh_callback is None:
            raise AppError(
                AUTHORIZATION_EXPIRED,
                "Access token is expired and no refresh callback is configured.",
                retryable=True,
            )
        payload = refresh_callback(record.refresh_token)
        access_token = str(payload["access_token"])
        refresh_token = str(payload.get("refresh_token") or record.refresh_token)
        expires_at = int(payload.get("expires_at") or now + int(payload.get("expires_in", 0)))
        refresh_expires_at = payload.get("refresh_expires_at", record.refresh_expires_at)
        self.save_tokens(account_id, access_token, refresh_token, expires_at, refresh_expires_at)
        return self.get_tokens(account_id)

    def delete_tokens(self, account_id: str) -> None:
        self.db.execute("DELETE FROM tokens WHERE account_id = ?", (account_id,))
