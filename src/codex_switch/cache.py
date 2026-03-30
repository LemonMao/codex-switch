from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from codex_switch.usage import UsageResponse, UsageSnapshot, parse_usage_response


@dataclass(slots=True)
class AppCache:
    version: int = 1
    selected_profile: str | None = None
    account_status_by_profile: dict[str, str] = field(default_factory=dict)
    usage_by_profile: dict[str, UsageSnapshot] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AppCache":
        account_status_payload = payload.get("account_status_by_profile") or {}
        usage_payload = payload.get("usage_by_profile") or {}
        account_status_by_profile = {
            name: status
            for name, status in account_status_payload.items()
            if isinstance(name, str) and isinstance(status, str) and status and status != "active"
        }
        usage_by_profile = {
            name: UsageSnapshot(
                fetched_at=int(snapshot.get("fetched_at", 0)),
                usage=parse_usage_response(snapshot.get("usage") or {}),
            )
            for name, snapshot in usage_payload.items()
        }
        return cls(
            version=int(payload.get("version", 1)),
            selected_profile=payload.get("selected_profile"),
            account_status_by_profile=account_status_by_profile,
            usage_by_profile=usage_by_profile,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "selected_profile": self.selected_profile,
            "account_status_by_profile": {
                name: status
                for name, status in self.account_status_by_profile.items()
                if status and status != "active"
            },
            "usage_by_profile": {
                name: {
                    "fetched_at": snapshot.fetched_at,
                    "usage": _usage_to_dict(snapshot.usage),
                }
                for name, snapshot in self.usage_by_profile.items()
            },
        }


def load_cache(path: Path) -> AppCache:
    if not path.exists():
        return AppCache()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return AppCache.from_dict(payload)


def save_cache(path: Path, cache: AppCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _usage_to_dict(usage: UsageResponse) -> dict[str, Any]:
    rate_limit = usage.rate_limit
    if rate_limit is None:
        rate_limit_payload = None
    else:
        rate_limit_payload = {
            "primary_window": _window_to_dict(rate_limit.primary_window),
            "secondary_window": _window_to_dict(rate_limit.secondary_window),
        }
    return {
        "email": usage.email,
        "plan_type": usage.plan_type,
        "rate_limit": rate_limit_payload,
    }


def _window_to_dict(window: Any) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "used_percent": window.used_percent,
        "limit_window_seconds": window.limit_window_seconds,
        "reset_after_seconds": window.reset_after_seconds,
        "reset_at": window.reset_at,
    }
