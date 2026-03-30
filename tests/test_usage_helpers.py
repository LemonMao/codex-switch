from types import SimpleNamespace

import pytest

import codex_switch.usage as usage_mod
from codex_switch.usage import (
    UsageRateLimit,
    UsageResponse,
    UsageWindow,
    fetch_usage,
    format_reset_eta,
    format_relative_age,
    parse_usage_response,
    pick_five_hour_window,
    pick_weekly_window,
    render_usage_bar,
)


def test_usage_helpers_cover_parser_and_fetch(monkeypatch):
    payload = {
        "email": "alpha@example.com",
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {
                "used_percent": 10.0,
                "limit_window_seconds": 18_000,
                "reset_after_seconds": 3_600,
                "reset_at": 18_000,
            },
            "secondary_window": {
                "used_percent": 62.5,
                "limit_window_seconds": 604_800,
                "reset_after_seconds": 183_600,
                "reset_at": 604_800,
            },
        },
    }
    usage = parse_usage_response(payload)

    assert pick_five_hour_window(usage).used_percent == 10.0
    assert pick_weekly_window(usage).used_percent == 62.5
    assert format_reset_eta(183_600) == "2d 3h"
    assert format_relative_age(32) == "32s ago"
    assert format_relative_age(183_600) == "2d 3h ago"
    assert render_usage_bar(62.5, width=10) == "━━━━━━┄┄┄┄"

    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: payload,
    )
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return fake_response

    monkeypatch.setattr(usage_mod.requests, "get", fake_get)

    fetched = fetch_usage("acct-123", "token-abc")

    assert fetched.email == "alpha@example.com"
    assert captured["url"] == usage_mod.USAGE_ENDPOINT
    assert captured["headers"]["ChatGPT-Account-Id"] == "acct-123"
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert captured["timeout"] == 30


def test_fetch_usage_raises_structured_error_for_account_deactivated_json(monkeypatch):
    payload = {
        "error": {
            "code": "account_deactivated",
            "message": "Your OpenAI account has been deactivated.",
        }
    }

    fake_response = SimpleNamespace(
        status_code=401,
        text='{"error":{"code":"account_deactivated","message":"Your OpenAI account has been deactivated."}}',
        json=lambda: payload,
        raise_for_status=lambda: None,
    )

    def fake_get(url, headers, timeout):
        return fake_response

    monkeypatch.setattr(usage_mod.requests, "get", fake_get)

    with pytest.raises(usage_mod.UsageRefreshError) as exc_info:
        fetch_usage("acct-123", "token-abc")

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "account_deactivated"
    assert "deactivated" in exc_info.value.response_text


def test_fetch_usage_raises_structured_error_for_account_deactivated_text(monkeypatch):
    fake_response = SimpleNamespace(
        status_code=401,
        text="account_deactivated",
        json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        raise_for_status=lambda: None,
    )

    def fake_get(url, headers, timeout):
        return fake_response

    monkeypatch.setattr(usage_mod.requests, "get", fake_get)

    with pytest.raises(usage_mod.UsageRefreshError) as exc_info:
        fetch_usage("acct-123", "token-abc")

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "account_deactivated"
    assert exc_info.value.response_text == "account_deactivated"
