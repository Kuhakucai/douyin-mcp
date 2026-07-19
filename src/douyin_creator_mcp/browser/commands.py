"""Pure-value command contracts for the single Playwright owner thread."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


PRIORITY_SHUTDOWN = 0
PRIORITY_USER = 10
PRIORITY_ACCOUNT = 20
PRIORITY_MEDIA = 30
PRIORITY_METADATA = 40


@dataclass(frozen=True, slots=True)
class BrowserCommand:
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    deadline_monotonic: float = field(default_factory=lambda: time.monotonic() + 30)
    priority: int = PRIORITY_METADATA


@dataclass(frozen=True, slots=True)
class LoginStart(BrowserCommand):
    headless: bool = False
    priority: int = PRIORITY_USER


@dataclass(frozen=True, slots=True)
class LoginStatus(BrowserCommand):
    priority: int = PRIORITY_USER


@dataclass(frozen=True, slots=True)
class SyncCreatorList(BrowserCommand):
    headless: bool = False
    priority: int = PRIORITY_METADATA


@dataclass(frozen=True, slots=True)
class SyncVideoDetails(BrowserCommand):
    videos: tuple[dict[str, Any], ...] = ()
    headless: bool = False
    priority: int = PRIORITY_METADATA


@dataclass(frozen=True, slots=True)
class VerifyAccount(BrowserCommand):
    account_id: str = ""
    target_video_id: str | None = None
    expected_video: dict[str, Any] | None = None
    priority: int = PRIORITY_ACCOUNT


@dataclass(frozen=True, slots=True)
class ObserveMediaBundle(BrowserCommand):
    account_id: str = ""
    target_video_id: str = ""
    platform_video_id: str | None = None
    full_window: bool = False
    priority: int = PRIORITY_MEDIA


@dataclass(frozen=True, slots=True)
class CloseSession(BrowserCommand):
    priority: int = PRIORITY_SHUTDOWN


@dataclass(frozen=True, slots=True)
class Shutdown(BrowserCommand):
    priority: int = PRIORITY_SHUTDOWN
