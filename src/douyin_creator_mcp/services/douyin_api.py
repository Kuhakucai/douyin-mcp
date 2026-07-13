"""Douyin OpenAPI client with mapping-driven request construction."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..api_mapping import ApiMapping, load_api_mapping
from ..config import Settings
from ..errors import (
    API_ERROR,
    API_RATE_LIMITED,
    DATA_NOT_AVAILABLE,
    INVALID_RESPONSE,
    NETWORK_ERROR,
    AppError,
)
from ..storage.db import Database
from ..storage.token_store import TokenStore


@dataclass(slots=True)
class PreparedRequest:
    method: str
    url: str
    headers: dict[str, str]
    params: dict[str, Any]
    json: dict[str, Any]
    data: dict[str, Any]


class DouyinApiClient:
    def __init__(
        self,
        settings: Settings,
        api_mapping: ApiMapping | None = None,
        db: Database | None = None,
        token_store: TokenStore | None = None,
        http_client: Any | None = None,
    ):
        self.settings = settings
        self.api_mapping = api_mapping or load_api_mapping(settings.api_mapping_file)
        self.db = db
        self.token_store = token_store
        self.http_client = http_client

    def _account_open_id(self, account_id: str) -> str:
        if self.db is None:
            return account_id
        row = self.db.query_one("SELECT open_id FROM accounts WHERE id = ?", (account_id,))
        return str(row["open_id"]) if row else account_id

    @staticmethod
    def _inject(target: dict[str, Any], location: str, field: str, value: str) -> None:
        if location == "header":
            header_name = "access-token" if field == "access_token" else field.replace("_", "-")
            target[header_name] = value
            return
        target[field] = value

    def build_request(
        self,
        api_key: str,
        access_token: str | None = None,
        open_id: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
    ) -> PreparedRequest:
        item = self.api_mapping.get(api_key)
        path = str(item.get("path", ""))
        if path.startswith("capability-only:"):
            raise AppError(
                DATA_NOT_AVAILABLE,
                f"{item.get('capability')} is a capability marker, not a callable API.",
                retryable=False,
            )
        prepared = PreparedRequest(
            method=str(item.get("method", "GET")).upper(),
            url=self.settings.douyin_base_url.rstrip("/") + "/" + path.lstrip("/"),
            headers={},
            params=dict(params or {}),
            json=dict(json_body or {}),
            data=dict(form or {}),
        )
        auth = item.get("auth", {}) or {}
        auth_values = {"access_token": access_token, "open_id": open_id}
        targets = {
            "query": prepared.params,
            "header": prepared.headers,
            "json": prepared.json,
            "form": prepared.data,
        }
        for field, location in auth.items():
            value = auth_values.get(field)
            if value:
                self._inject(targets[str(location)], str(location), str(field), value)
        return prepared

    def request(
        self,
        api_key: str,
        account_id: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        access_token = None
        open_id = None
        if account_id:
            if self.token_store is None:
                raise AppError(API_ERROR, "Token store is not configured.")
            token = self.token_store.get_valid_token(account_id, self.refresh_access_token)
            access_token = token.access_token
            open_id = self._account_open_id(account_id)

        prepared = self.build_request(
            api_key,
            access_token=access_token,
            open_id=open_id,
            params=params,
            json_body=json_body,
            form=form,
        )
        try:
            if self.http_client is not None:
                response = self.http_client.request(
                    prepared.method,
                    prepared.url,
                    headers=prepared.headers,
                    params=prepared.params,
                    json=prepared.json or None,
                    data=prepared.data or None,
                    timeout=self.settings.http_timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.settings.http_timeout_seconds) as client:
                    response = client.request(
                        prepared.method,
                        prepared.url,
                        headers=prepared.headers,
                        params=prepared.params,
                        json=prepared.json or None,
                        data=prepared.data or None,
                    )
        except httpx.HTTPError as exc:
            raise AppError(NETWORK_ERROR, str(exc), retryable=True) from exc
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> dict[str, Any]:
        status_code = int(getattr(response, "status_code", 200))
        if status_code == 429:
            raise AppError(API_RATE_LIMITED, "Douyin OpenAPI rate limit exceeded.", retryable=True)
        if status_code >= 400:
            raise AppError(API_ERROR, f"Douyin OpenAPI returned HTTP {status_code}.", retryable=status_code >= 500)
        try:
            payload = response.json() if hasattr(response, "json") else response
        except ValueError as exc:
            raise AppError(INVALID_RESPONSE, "Douyin OpenAPI returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise AppError(INVALID_RESPONSE, "Douyin OpenAPI response must be a JSON object.")
        data = payload.get("data")
        if isinstance(data, dict):
            error_code = data.get("error_code")
            if error_code not in (None, 0, "0"):
                raise AppError(API_ERROR, str(data.get("description") or data.get("message") or payload), retryable=False)
        error_code = payload.get("error_code")
        if error_code not in (None, 0, "0"):
            raise AppError(API_ERROR, str(payload.get("description") or payload.get("message") or payload), retryable=False)
        return payload

    @staticmethod
    def _unwrap_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        payload = self.request(
            "oauth_access_token",
            form={
                "client_key": self.settings.douyin_client_key,
                "client_secret": self.settings.douyin_client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        return self.normalize_token_payload(payload)

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        payload = self.request(
            "oauth_refresh_token",
            form={
                "client_key": self.settings.douyin_client_key,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        return self.normalize_token_payload(payload)

    def renew_refresh_token(self, refresh_token: str) -> dict[str, Any]:
        payload = self.request(
            "oauth_renew_refresh_token",
            form={
                "client_key": self.settings.douyin_client_key,
                "refresh_token": refresh_token,
            },
        )
        return self.normalize_token_payload(payload)

    def get_user_info(self, account_id: str) -> dict[str, Any]:
        payload = self.request("get_user_info", account_id=account_id)
        data = self._unwrap_data(payload)
        return {
            "open_id": data.get("open_id") or data.get("open_id_str") or account_id,
            "nickname": data.get("nickname") or data.get("nick_name"),
            "avatar": data.get("avatar") or data.get("avatar_url"),
        }

    @staticmethod
    def normalize_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
        data = DouyinApiClient._unwrap_data(payload)
        now = int(time.time())
        expires_in = int(data.get("expires_in") or data.get("expires") or 0)
        refresh_expires_in = data.get("refresh_expires_in")
        return {
            "open_id": data.get("open_id"),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_in": expires_in,
            "expires_at": int(data.get("expires_at") or (now + expires_in if expires_in else now)),
            "refresh_expires_at": int(data.get("refresh_expires_at") or (now + int(refresh_expires_in) if refresh_expires_in else 0)) or None,
            "scope": data.get("scope") or data.get("scopes"),
            "nickname": data.get("nickname"),
            "avatar": data.get("avatar"),
        }
