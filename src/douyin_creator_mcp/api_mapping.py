"""Load and query API mapping definitions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import CONFIGURATION_ERROR, AppError


DEFAULT_API_MAPPING: dict[str, dict[str, Any]] = {
    "oauth_access_token": {
        "method": "POST",
        "path": "/oauth/access_token/",
        "scope": None,
        "capability": "oauth.access_token",
        "auth": {},
        "request": {"form": ["client_key", "client_secret", "code", "grant_type"]},
        "mvp_status": "required",
    },
    "oauth_refresh_token": {
        "method": "POST",
        "path": "/oauth/refresh_token/",
        "scope": None,
        "capability": "oauth.refresh_token",
        "auth": {},
        "request": {"form": ["client_key", "grant_type", "refresh_token"]},
        "mvp_status": "required",
    },
    "oauth_renew_refresh_token": {
        "method": "POST",
        "path": "/oauth/renew_refresh_token/",
        "scope": None,
        "capability": "oauth.renew_refresh_token",
        "auth": {},
        "request": {"form": ["client_key", "refresh_token"]},
        "mvp_status": "recommended",
    },
    "get_user_info": {
        "method": "GET",
        "path": "/oauth/userinfo/",
        "scope": "user_info",
        "capability": "user_info",
        "auth": {"access_token": "query", "open_id": "query"},
        "request": {"params": []},
        "mvp_status": "required",
    },
}


@dataclass(frozen=True, slots=True)
class ApiMapping:
    items: Mapping[str, dict[str, Any]]

    def get(self, api_key: str) -> dict[str, Any]:
        try:
            return dict(self.items[api_key])
        except KeyError as exc:
            raise AppError(CONFIGURATION_ERROR, f"Unknown API mapping: {api_key}") from exc

    def capability_items(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for key, value in self.items.items():
            capability = value.get("capability")
            if capability and not str(capability).startswith("oauth."):
                item = dict(value)
                item["api_key"] = key
                result[str(capability)] = item
        return result


def _extract_json_block(text: str) -> str | None:
    marker_index = text.find("<!-- api-mapping-json -->")
    search_area = text[marker_index:] if marker_index >= 0 else text
    match = re.search(r"```json\s*(\{.*?\})\s*```", search_area, re.DOTALL)
    return match.group(1) if match else None


def load_api_mapping(path: Path | str | None = None) -> ApiMapping:
    if path is None:
        return ApiMapping(DEFAULT_API_MAPPING)
    mapping_path = Path(path)
    if not mapping_path.exists():
        return ApiMapping(DEFAULT_API_MAPPING)
    text = mapping_path.read_text(encoding="utf-8")
    block = _extract_json_block(text)
    if not block:
        raise AppError(CONFIGURATION_ERROR, f"No api-mapping-json block found in {mapping_path}")
    try:
        payload = json.loads(block)
    except json.JSONDecodeError as exc:
        raise AppError(CONFIGURATION_ERROR, f"Invalid JSON API mapping in {mapping_path}") from exc
    if not isinstance(payload, dict):
        raise AppError(CONFIGURATION_ERROR, "API mapping root must be an object.")
    return ApiMapping(payload)
