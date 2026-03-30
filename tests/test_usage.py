from codex_switch.usage import (
    UsageRateLimit,
    UsageResponse,
    UsageWindow,
    format_reset_eta,
    pick_weekly_window,
    render_usage_bar,
)


def test_usage_helpers_pick_weekly_window_and_format_eta():
    usage = UsageResponse(
        email="alpha@example.com",
        plan_type="plus",
        rate_limit=UsageRateLimit(
            primary_window=UsageWindow(
                used_percent=12.0,
                limit_window_seconds=18_000,
                reset_after_seconds=7_200,
                reset_at=7_200,
            ),
            secondary_window=UsageWindow(
                used_percent=62.5,
                limit_window_seconds=604_800,
                reset_after_seconds=183_600,
                reset_at=604_800,
            ),
        ),
    )

    weekly = pick_weekly_window(usage)

    assert weekly is usage.rate_limit.secondary_window
    assert format_reset_eta(183_600) == "2d 3h"
    assert render_usage_bar(62.5, width=10) == "━━━━━━┄┄┄┄"
