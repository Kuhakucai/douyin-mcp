"""Capability probing and caching."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..api_mapping import ApiMapping
from ..errors import CAPABILITY_MISSING, AppError
from ..storage.db import Database
from .account_service import AccountService


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CapabilityService:
    def __init__(self, db: Database, api_mapping: ApiMapping, account_service: AccountService):
        self.db = db
        self.api_mapping = api_mapping
        self.account_service = account_service

    def record_capability(
        self,
        account_id: str,
        capability_key: str,
        status: str,
        scope: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        capability_id = f"{account_id}:{capability_key}" if account_id else str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO api_capabilities (id, account_id, capability_key, scope, status, last_checked_at, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status=excluded.status,
              last_checked_at=excluded.last_checked_at,
              detail_json=excluded.detail_json
            """,
            (
                capability_id,
                account_id,
                capability_key,
                scope,
                status,
                utc_now_iso(),
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )

    def check_capabilities(self, account_id: str) -> dict[str, Any]:
        account = self.account_service.get_account(account_id)
        scopes = set(account.get("authorized_scopes") or [])
        capabilities: dict[str, str] = {}
        missing: list[str] = []
        unknown: list[str] = []
        for capability_key, item in self.api_mapping.capability_items().items():
            scope = item.get("scope")
            mvp_status = item.get("mvp_status")
            if not scope:
                status = "available"
            elif scope not in scopes:
                status = "missing"
            elif mvp_status == "required":
                status = "available"
            elif mvp_status in {"after_permission", "optional"}:
                status = "unknown"
            else:
                status = "limited"
            capabilities[capability_key] = status
            if status == "missing":
                missing.append(capability_key)
            if status == "unknown":
                unknown.append(capability_key)
            self.record_capability(
                account_id,
                capability_key,
                status,
                str(scope) if scope else None,
                {"api_key": item.get("api_key"), "mvp_status": mvp_status},
            )
        return {
            "account_id": account_id,
            "capabilities": capabilities,
            "missing_capabilities": missing,
            "unknown_capabilities": unknown,
        }

    def ensure(self, account_id: str, capability_key: str) -> None:
        row = self.db.query_one(
            """
            SELECT status, scope FROM api_capabilities
            WHERE account_id = ? AND capability_key = ?
            ORDER BY last_checked_at DESC
            LIMIT 1
            """,
            (account_id, capability_key),
        )
        status = row["status"] if row else self.check_capabilities(account_id)["capabilities"].get(capability_key)
        if status != "available":
            raise AppError(
                CAPABILITY_MISSING,
                f"Capability {capability_key} is not available for this account.",
                retryable=False,
                extra={"required_capability": capability_key, "current_status": status},
            )
