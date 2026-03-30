from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

USAGE_ENDPOINT = "https://chatgpt.com/backend-api/wham/usage"


@dataclass(slots=True)
class UsageWindow:
    used_percent: float
    limit_window_seconds: int
    reset_after_seconds: int
    reset_at: int


@dataclass(slots=True)
class UsageRateLimit:
    primary_window: UsageWindow | None = None
    secondary_window: UsageWindow | None = None


@dataclass(slots=True)
class UsageResponse:
    email: str | None = None
    plan_type: str | None = None
    rate_limit: UsageRateLimit | None = None


@dataclass(slots=True)
class UsageSnapshot:
    fetched_at: int
    usage: UsageResponse


class UsageRefreshError(Exception):
    def __init__(self, *, status_code: int, error_code: str | None, response_text: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.response_text = response_text
        details = error_code or response_text or "unknown usage refresh error"
        super().__init__(f"usage refresh failed ({status_code}): {details}")


def pick_five_hour_window(usage: UsageResponse) -> UsageWindow | None:
    rate_limit = usage.rate_limit
    if rate_limit is None:
        return None
    if rate_limit.primary_window and rate_limit.primary_window.limit_window_seconds == 18_000:
        return rate_limit.primary_window
    if rate_limit.secondary_window and rate_limit.secondary_window.limit_window_seconds == 18_000:
        return rate_limit.secondary_window
    return None


def pick_weekly_window(usage: UsageResponse) -> UsageWindow | None:
    rate_limit = usage.rate_limit
    if rate_limit is None:
        return None
    if rate_limit.secondary_window and rate_limit.secondary_window.limit_window_seconds == 604_800:
        return rate_limit.secondary_window
    if rate_limit.primary_window and rate_limit.primary_window.limit_window_seconds == 604_800:
        return rate_limit.primary_window
    return None


def format_reset_eta(seconds: int) -> str:
    remaining = max(0, int(seconds))
    days, remainder = divmod(remaining, 86_400)
    hours, _ = divmod(remainder, 3_600)
    if days and hours:
        return f"{days}d {hours}h"
    if days:
        return f"{days}d"
    return f"{hours}h"


def format_relative_age(seconds: int) -> str:
    remaining = max(0, int(seconds))
    days, remainder = divmod(remaining, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        if hours:
            return f"{days}d {hours}h ago"
        return f"{days}d ago"
    if hours:
        if minutes:
            return f"{hours}h {minutes}m ago"
        return f"{hours}h ago"
    if minutes:
        if seconds:
            return f"{minutes}m {seconds}s ago"
        return f"{minutes}m ago"
    return f"{seconds}s ago"


def render_usage_bar(used_percent: float, width: int = 24) -> str:
    width = max(1, int(width))
    filled = round(width * max(0.0, min(100.0, used_percent)) / 100.0)
    filled = max(0, min(width, filled))
    return "━" * filled + "┄" * (width - filled)


def parse_usage_response(payload: dict[str, Any]) -> UsageResponse:
    rate_limit_payload = payload.get("rate_limit") or {}
    return UsageResponse(
        email=payload.get("email"),
        plan_type=payload.get("plan_type"),
        rate_limit=UsageRateLimit(
            primary_window=_parse_window(rate_limit_payload.get("primary_window")),
            secondary_window=_parse_window(rate_limit_payload.get("secondary_window")),
        )
        if rate_limit_payload
        else None,
    )


def fetch_usage(account_id: str, access_token: str) -> UsageResponse:
    response = requests.get(
        USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-Id": account_id,
            "User-Agent": "codex-switch",
        },
        timeout=30,
    )
    if getattr(response, "status_code", None) == 401:
        error_code, response_text = _extract_usage_error(response)
        if error_code == "account_deactivated":
            raise UsageRefreshError(
                status_code=401,
                error_code=error_code,
                response_text=response_text,
            )
    response.raise_for_status()
    return parse_usage_response(response.json())


def _parse_window(payload: Any) -> UsageWindow | None:
    if not payload:
        return None
    return UsageWindow(
        used_percent=float(payload.get("used_percent", 0.0)),
        limit_window_seconds=int(payload.get("limit_window_seconds", 0)),
        reset_after_seconds=int(payload.get("reset_after_seconds", 0)),
        reset_at=int(payload.get("reset_at", 0)),
    )


def _extract_usage_error(response: Any) -> tuple[str | None, str]:
    response_text = getattr(response, "text", "") or ""
    error_code: str | None = None

    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code.strip():
                error_code = code

    if error_code is None and "account_deactivated" in response_text:
        error_code = "account_deactivated"

    return error_code, response_text
