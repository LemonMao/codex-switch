import asyncio
from types import SimpleNamespace

from codex_switch.cache import AppCache
from codex_switch.cli import build_parser, main
from codex_switch.config import AppPaths
from codex_switch.controller import AccountController, DialogState
from codex_switch.ui import CodexSwitchApp
from codex_switch.usage import UsageRateLimit, UsageResponse, UsageSnapshot, UsageWindow


def test_cli_main_import_and_default_launch(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    legacy_accounts = home / ".codex" / "accounts"
    legacy_accounts.mkdir(parents=True)
    (legacy_accounts / "alpha.json").write_text('{"token":"alpha"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_SWITCH_HOME", str(home))

    parser = build_parser()
    assert parser.prog == "codex-switch"
    assert parser.parse_args(["import"]).command == "import"
    assert main(["import"]) == 0
    assert capsys.readouterr().out.strip() == "alpha"

    seen = {}

    class FakeApp:
        def __init__(self, controller):
            seen["controller"] = controller

        def run(self):
            seen["ran"] = True

    import codex_switch.ui as ui_mod

    monkeypatch.setattr(ui_mod, "CodexSwitchApp", FakeApp)
    assert main([]) == 0
    assert seen["ran"] is True
    assert isinstance(seen["controller"], AccountController)


def test_ui_starts_without_hidden_focus_and_shortcuts_work(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    app = CodexSwitchApp(controller)

    def fake_run_worker(*_args, **_kwargs):
        return None

    app.run_worker = fake_run_worker  # type: ignore[method-assign]

    async def exercise() -> None:
        async with app.run_test() as pilot:
            assert app.focused is None
            await pilot.press("j")
            assert controller.selected_index == 1
            assert controller.selected_row.name == "work"
            await pilot.press("q")
            assert app.is_running is False

    asyncio.run(exercise())


def test_ui_starts_background_refresh_on_mount(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "appletree.json").write_text(
        '{"tokens":{"account_id":"acct-appletree","access_token":"tok-appletree"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    app = CodexSwitchApp(controller)
    seen = {}
    scheduled = {}

    def fake_refresh_views() -> None:
        seen["refreshed"] = True

    def fake_run_worker(work, **kwargs):
        seen["work"] = work
        seen["kwargs"] = kwargs
        return None

    def fake_call_after_refresh(callback, *args, **kwargs):
        scheduled["callback"] = callback
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return True

    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]
    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]

    app.on_mount()

    assert seen["refreshed"] is True
    assert "kwargs" not in seen
    assert scheduled["kwargs"]["name"] == "profile-usage-refresh"
    scheduled["callback"](*scheduled["args"], **scheduled["kwargs"])
    assert seen["kwargs"]["thread"] is True
    assert seen["kwargs"]["exclusive"] is True


def test_ui_registers_periodic_refresh_on_mount(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    app = CodexSwitchApp(controller)

    intervals: list[float] = []

    def fake_refresh_views() -> None:
        pass

    def fake_set_interval(interval, callback):
        intervals.append(interval)
        return callback

    app._start_background_refresh = lambda: None  # type: ignore[method-assign]
    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.set_interval = fake_set_interval  # type: ignore[method-assign]
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())

    app.on_mount()

    assert 0.12 in intervals
    assert 300 in intervals


def test_ui_periodic_refresh_triggers_full_refresh(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.refresh_all_profiles = lambda: 0  # type: ignore[method-assign]
    app = CodexSwitchApp(controller)

    intervals: list[tuple[float, object]] = []
    scheduled = {}

    def fake_refresh_views() -> None:
        pass

    def fake_set_interval(interval, callback):
        intervals.append((interval, callback))
        return callback

    def fake_run_worker(work, **kwargs):
        scheduled["work"] = work
        scheduled["kwargs"] = kwargs
        return None

    def fake_call_after_refresh(callback, *args, **kwargs):
        scheduled["callback"] = callback
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return True

    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.set_interval = fake_set_interval  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]
    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]
    app.call_from_thread = lambda callback: callback()  # type: ignore[method-assign]
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())

    app.on_mount()

    periodic_callback = next(callback for interval, callback in intervals if interval == 300)
    scheduled["callback"](*scheduled["args"], **scheduled["kwargs"])
    scheduled["work"]()
    scheduled.clear()

    periodic_callback()

    assert scheduled["kwargs"]["name"] == "profile-usage-refresh"
    assert scheduled["kwargs"]["group"] == "profile-usage"


def test_ui_periodic_refresh_skips_when_refreshing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    app = CodexSwitchApp(controller)

    intervals: list[tuple[float, object]] = []
    scheduled = {}

    def fake_refresh_views() -> None:
        pass

    def fake_set_interval(interval, callback):
        intervals.append((interval, callback))
        return callback

    def fake_run_worker(work, **kwargs):
        scheduled["work"] = work
        scheduled["kwargs"] = kwargs
        return None

    def fake_call_after_refresh(callback, *args, **kwargs):
        scheduled["callback"] = callback
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return True

    app._start_background_refresh = lambda: None  # type: ignore[method-assign]
    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.set_interval = fake_set_interval  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]
    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())

    app.on_mount()

    periodic_callback = next(callback for interval, callback in intervals if interval == 300)
    app._status_activity = "Refreshing profile usage"

    periodic_callback()

    assert scheduled == {}


def test_ui_shows_spinner_while_background_refresh_runs(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    app = CodexSwitchApp(controller)

    status_history: list[str] = []
    seen = {}
    scheduled = {}

    def fake_refresh_views() -> None:
        status_history.append(str(app._render_status()))

    def fake_run_worker(work, **kwargs):
        seen["work"] = work
        seen["kwargs"] = kwargs
        return None

    def fake_call_after_refresh(callback, *args, **kwargs):
        scheduled["callback"] = callback
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return True

    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]
    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]
    app.call_from_thread = lambda callback: callback()  # type: ignore[method-assign]
    controller.refresh_all_profiles = lambda: 0  # type: ignore[method-assign]

    app._start_background_refresh()

    assert scheduled["kwargs"]["name"] == "profile-usage-refresh"
    assert status_history[0].splitlines()[0].startswith("| Refreshing profile usage")
    assert "j/k move" in status_history[0]

    scheduled["callback"](*scheduled["args"], **scheduled["kwargs"])
    assert seen["kwargs"]["name"] == "profile-usage-refresh"
    seen["work"]()

    assert status_history[-1].splitlines()[0] == "Ready"
    assert "j/k move" in status_history[-1]


def test_ui_refresh_selected_runs_in_background_and_updates_status(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    app = CodexSwitchApp(controller)

    status_history: list[str] = []
    seen = {}
    scheduled = {}

    def fake_refresh_views() -> None:
        status_history.append(str(app._render_status()))

    def fake_run_worker(work, **kwargs):
        seen["work"] = work
        seen["kwargs"] = kwargs
        return None

    def fake_call_after_refresh(callback, *args, **kwargs):
        scheduled["callback"] = callback
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return True

    controller.refresh_selected = lambda: seen.__setitem__("refreshed", True)  # type: ignore[method-assign]
    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]
    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]
    app.call_from_thread = lambda callback: callback()  # type: ignore[method-assign]

    app.action_refresh_selected()

    assert seen.get("refreshed") is None
    assert status_history[0].splitlines()[0].startswith("| Refreshing profile usage")
    assert "j/k move" in status_history[0]

    assert scheduled["kwargs"]["name"] == "profile-refresh"
    scheduled["callback"](*scheduled["args"], **scheduled["kwargs"])
    assert seen["kwargs"]["name"] == "profile-refresh"

    seen["work"]()

    assert seen["refreshed"] is True
    assert status_history[-1].splitlines()[0] == "Ready"
    assert "j/k move" in status_history[-1]


def test_ui_refresh_selected_accepts_uppercase_u(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    app = CodexSwitchApp(controller)

    scheduled = {}

    def fake_refresh_views() -> None:
        pass

    def fake_run_worker(work, **kwargs):
        scheduled["work"] = work
        scheduled["kwargs"] = kwargs
        return None

    def fake_call_after_refresh(callback, *args, **kwargs):
        scheduled["callback"] = callback
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return True

    app._start_background_refresh = lambda: None  # type: ignore[method-assign]
    app._refresh_views = fake_refresh_views  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]
    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]

    async def exercise() -> None:
        async with app.run_test() as pilot:
            await pilot.press("U")
            await pilot.press("q")

    asyncio.run(exercise())

    assert scheduled["kwargs"]["name"] == "profile-refresh"


def test_ui_shows_unsaved_profile_hint_and_prefills_save_prompt(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        usage_by_profile={
            "__current__": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="lemontree7718@proton.me",
                    plan_type="free",
                ),
            )
        }
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache, now_seconds=lambda: 1_000_032)
    controller.reload()

    assert controller.selected_row is not None
    assert controller.selected_row.is_unsaved is True

    app = CodexSwitchApp(controller)

    status = str(app._render_status())

    assert "Found an unsaved profile" in status
    assert "lemontree7718" in status

    seen = {}
    prompt = SimpleNamespace(
        display=False,
        disabled=True,
        value="",
        placeholder="",
        focused=False,
        focus=lambda: seen.__setitem__("prompt_focused", True),
    )
    panels = {
        "#profiles": SimpleNamespace(update=lambda renderable: seen.__setitem__("profiles", str(renderable))),
        "#details": SimpleNamespace(update=lambda renderable: seen.__setitem__("details", str(renderable))),
        "#status": SimpleNamespace(update=lambda renderable: seen.__setitem__("status", str(renderable))),
        "#prompt": prompt,
    }
    app.query_one = lambda selector, widget_type: panels[selector]

    controller.dialog = DialogState(kind="save")
    app._refresh_views()

    assert prompt.display is True
    assert prompt.disabled is False
    assert prompt.value == "lemontree7718"
    assert prompt.placeholder == "Save current profile as..."
    assert seen["prompt_focused"] is True

    prompt.value = "custom-name"
    app.on_input_changed(SimpleNamespace(value="custom-name"))
    app._refresh_views()

    assert prompt.value == "custom-name"


def test_ui_renders_usage_summary_and_prompt_flow(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "appletree.json").write_text(
        '{"tokens":{"account_id":"acct-appletree","access_token":"tok-appletree"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        usage_by_profile={
            "appletree": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="appletree8819@proton.me",
                    plan_type="free",
                    rate_limit=UsageRateLimit(
                        secondary_window=UsageWindow(
                            used_percent=27.4,
                            limit_window_seconds=604_800,
                            reset_after_seconds=568_800,
                            reset_at=604_800,
                        )
                    ),
                ),
            )
        }
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache, now_seconds=lambda: 1_000_032)
    controller.repository.switch_to_profile("appletree")
    controller.reload()

    app = CodexSwitchApp(controller)

    details = str(app._render_details())
    status = str(app._render_status())

    assert "Profile: appletree" in details
    assert "Service: Codex [cx]" in details
    assert "State: current" in details
    assert "Last updated: 32s ago" in details
    assert "Email: appletree8819@proton.me" in details
    assert "Plan: free" in details
    assert "Weekly: 27% used, reset in 6d 14h" in details
    assert "j/k move" in status
    assert "q quit" in status
    assert app._prompt_placeholder(DialogState(kind="save")) == "Save current profile as..."

    seen = {}
    prompt = SimpleNamespace(
        display=False,
        disabled=True,
        value="",
        placeholder="",
        focused=False,
        focus=lambda: seen.__setitem__("prompt_focused", True),
    )
    panels = {
        "#profiles": SimpleNamespace(update=lambda renderable: seen.__setitem__("profiles", str(renderable))),
        "#details": SimpleNamespace(update=lambda renderable: seen.__setitem__("details", str(renderable))),
        "#status": SimpleNamespace(update=lambda renderable: seen.__setitem__("status", str(renderable))),
        "#prompt": prompt,
    }
    app.query_one = lambda selector, widget_type: panels[selector]
    app._refresh_views()

    assert prompt.display is False
    assert prompt.disabled is True

    controller.dialog = DialogState(kind="rename", target_name="appletree")
    app._refresh_views()

    assert prompt.display is True
    assert prompt.disabled is False
    assert prompt.placeholder == "Rename appletree to..."
    assert seen["prompt_focused"] is True

    app.on_input_submitted(SimpleNamespace(value="personal"))
    assert controller.dialog is None


def test_ui_renders_current_deactivated_profile_and_preserves_usage_snapshot(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "appletree.json").write_text(
        '{"tokens":{"account_id":"acct-appletree","access_token":"tok-appletree"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        account_status_by_profile={"appletree": "deactivated"},
        usage_by_profile={
            "appletree": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="appletree8819@proton.me",
                    plan_type="free",
                    rate_limit=UsageRateLimit(
                        secondary_window=UsageWindow(
                            used_percent=27.4,
                            limit_window_seconds=604_800,
                            reset_after_seconds=568_800,
                            reset_at=604_800,
                        )
                    ),
                ),
            )
        },
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache, now_seconds=lambda: 1_000_032)
    controller.repository.switch_to_profile("appletree")
    controller.reload()

    app = CodexSwitchApp(controller)
    rendered = str(app._render_profiles())
    details = str(app._render_details())
    line = rendered.splitlines()[0]

    assert line.startswith("❯ * appletree")
    assert "✕ deactivated" in line
    assert "[current]" not in line
    assert "[backup]" not in line
    assert "[unsaved]" not in line
    assert "27.4%, reset after 6d 14h" in line
    assert "State: ✕ deactivated" in details
    assert "Last updated: 32s ago" in details
    assert "Email: appletree8819@proton.me" in details
    assert "Plan: free" in details
    assert "Weekly: 27% used, reset in 6d 14h" in details


def test_ui_renders_unsaved_deactivated_profile_and_preserves_usage_snapshot(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        account_status_by_profile={"__current__": "deactivated"},
        usage_by_profile={
            "__current__": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="lemontree7718@proton.me",
                    plan_type="free",
                    rate_limit=UsageRateLimit(
                        secondary_window=UsageWindow(
                            used_percent=12.0,
                            limit_window_seconds=604_800,
                            reset_after_seconds=3_600,
                            reset_at=604_800,
                        )
                    ),
                ),
            )
        },
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache, now_seconds=lambda: 1_000_032)
    controller.reload()

    app = CodexSwitchApp(controller)
    rendered = str(app._render_profiles())
    details = str(app._render_details())
    line = rendered.splitlines()[0]

    assert line.startswith("❯ * Current (unsaved)")
    assert "✕ deactivated" in line
    assert "[current]" not in line
    assert "[backup]" not in line
    assert "[unsaved]" not in line
    assert "12.0%, reset after 1h" in line
    assert "State: ✕ deactivated" in details
    assert "Last updated: 32s ago" in details
    assert "Email: lemontree7718@proton.me" in details
    assert "Plan: free" in details
    assert "Weekly: 12% used, reset in 1h" in details


def test_ui_uses_profile_color_for_detail_heading(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()

    app = CodexSwitchApp(controller)

    assert app._detail_profile_style("lemontree") == f"bold {app._profile_color('lemontree')}"


def test_ui_defaults_to_tokyo_night_theme(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    app = CodexSwitchApp(
        AccountController(
            AppPaths.from_home(home),
        )
    )

    assert app.theme == "tokyo-night"
    assert app.current_theme.name == "tokyo-night"
    assert "$background" in CodexSwitchApp.CSS
    assert "$foreground-muted" in CodexSwitchApp.CSS


def test_ui_status_colors_follow_current_theme(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    app = CodexSwitchApp(controller)

    app.theme = "textual-light"
    status = app._render_status()

    assert status.spans[0].style == app.get_css_variables()["foreground-muted"]


def test_ui_renders_profile_colors_and_aligned_usage_rows(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "appletree.json").write_text(
        '{"tokens":{"account_id":"acct-appletree","access_token":"tok-appletree"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "lemontree.json").write_text(
        '{"tokens":{"account_id":"acct-lemontree","access_token":"tok-lemontree"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        usage_by_profile={
            "appletree": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="appletree8819@proton.me",
                    plan_type="free",
                    rate_limit=UsageRateLimit(
                        secondary_window=UsageWindow(
                            used_percent=32.0,
                            limit_window_seconds=604_800,
                            reset_after_seconds=90_000,
                            reset_at=604_800,
                        )
                    ),
                ),
            ),
            "lemontree": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="lemontree@example.com",
                    plan_type="plus",
                    rate_limit=UsageRateLimit(
                        secondary_window=UsageWindow(
                            used_percent=12.0,
                            limit_window_seconds=604_800,
                            reset_after_seconds=3_600,
                            reset_at=604_800,
                        )
                    ),
                ),
            ),
        }
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache, now_seconds=lambda: 1_000_032)
    controller.repository.switch_to_profile("appletree")
    controller.cache.selected_profile = "lemontree"
    controller.reload()

    app = CodexSwitchApp(controller)
    rendered = str(app._render_profiles())
    lines = rendered.splitlines()

    assert app._profile_color("appletree") != app._profile_color("lemontree")
    assert lines[0].startswith("  * appletree")
    assert lines[1].startswith("❯   lemontree")
    assert "[current]" in lines[0]
    assert "[backup]" in lines[1]
    assert "32.0%, reset after 1d 1h" in lines[0]
    assert "12.0%, reset after 1h" in lines[1]
    assert lines[0].index("━") == lines[1].index("━")


def test_ui_defaults_to_tokyo_night_theme_palette():
    assert CodexSwitchApp.TOKYO_NIGHT["screen"] == "#1a1b26"
    assert CodexSwitchApp.TOKYO_NIGHT["panel"] == "#24283b"
    assert CodexSwitchApp.TOKYO_NIGHT["accent"] == "#7aa2f7"
    assert CodexSwitchApp.TOKYO_NIGHT["muted"] == "#9aa5ce"
