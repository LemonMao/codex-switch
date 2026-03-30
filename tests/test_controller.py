import requests

from codex_switch.cache import AppCache
from codex_switch.config import AppPaths
from codex_switch.controller import AccountController, DialogState
from codex_switch.usage import UsageRateLimit, UsageRefreshError, UsageResponse, UsageSnapshot, UsageWindow


def test_controller_saves_unsaved_current_profile_and_switches(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()

    assert controller.rows[0].key == "__current__"
    assert controller.selected_row.key == "__current__"

    controller.move_selection(1)
    assert controller.selected_row.key == "work"

    controller.enter()
    assert controller.dialog is None
    assert controller.current_profile_name() == "work"
    assert controller.paths.auth_path.is_symlink()


def test_controller_renames_and_deletes_current_profile(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.repository.switch_to_profile("work")
    controller.reload()

    controller.request_rename()
    controller.submit_dialog("personal")

    assert (snapshots_dir / "personal.json").exists()
    assert not (snapshots_dir / "work.json").exists()
    assert controller.current_profile_name() == "personal"

    controller.request_delete()
    controller.confirm_delete(True)

    assert not (snapshots_dir / "personal.json").exists()
    assert not controller.paths.auth_path.is_symlink()
    assert controller.current_profile_name() is None


def test_controller_reports_duplicate_save_without_crashing(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.dialog = DialogState(kind="save")

    controller.submit_dialog("work")

    assert controller.dialog is not None
    assert controller.dialog.kind == "save"
    assert controller.status_message is not None
    assert "already exists" in controller.status_message
    assert (snapshots_dir / "work.json").read_text(encoding="utf-8") == (
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n'
    )


def test_controller_reports_duplicate_rename_without_crashing(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "office.json").write_text(
        '{"tokens":{"account_id":"acct-office","access_token":"tok-office"}}\n',
        encoding="utf-8",
    )
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.move_selection(2)
    controller.request_rename()

    controller.submit_dialog("office")

    assert controller.dialog is not None
    assert controller.dialog.kind == "rename"
    assert controller.dialog.target_name == "work"
    assert controller.status_message is not None
    assert "already exists" in controller.status_message
    assert (snapshots_dir / "work.json").exists()
    assert (snapshots_dir / "office.json").exists()


def test_controller_refreshes_selected_profile_usage(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.set_usage_fetcher(
        lambda account_id, access_token: UsageResponse(
            email="alpha@example.com",
            plan_type="plus",
            rate_limit=UsageRateLimit(
                secondary_window=UsageWindow(
                    used_percent=62.5,
                    limit_window_seconds=604800,
                    reset_after_seconds=183600,
                    reset_at=604800,
                )
            ),
        )
    )

    controller.refresh_selected()

    assert controller.selected_row.usage is not None
    assert controller.selected_row.usage.usage.email == "alpha@example.com"


def test_controller_marks_deactivated_profile_without_losing_usage(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        usage_by_profile={
            "work": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="alpha@example.com",
                    plan_type="plus",
                ),
            )
        }
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache)
    controller.reload()
    controller.set_usage_fetcher(
        lambda *_: (_ for _ in ()).throw(
            UsageRefreshError(
                status_code=401,
                error_code="account_deactivated",
                response_text="account_deactivated",
            )
        )
    )

    controller.refresh_selected()

    assert controller.status_message is None
    assert controller.cache.account_status_by_profile["work"] == "deactivated"
    assert controller.selected_row.account_status == "deactivated"
    assert controller.selected_row.usage is not None
    assert controller.selected_row.usage.usage.email == "alpha@example.com"


def test_controller_marks_deactivated_profile_without_prior_usage(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.set_usage_fetcher(
        lambda *_: (_ for _ in ()).throw(
            UsageRefreshError(
                status_code=401,
                error_code="account_deactivated",
                response_text="account_deactivated",
            )
        )
    )

    controller.refresh_selected()

    assert controller.status_message is None
    assert controller.cache.account_status_by_profile["work"] == "deactivated"
    assert controller.selected_row.account_status == "deactivated"
    assert controller.selected_row.usage is None


def test_controller_keeps_deactivated_status_on_non_deactivation_failure(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    cache = AppCache(
        account_status_by_profile={"work": "deactivated"},
        usage_by_profile={
            "work": UsageSnapshot(
                fetched_at=1_000_000,
                usage=UsageResponse(
                    email="alpha@example.com",
                    plan_type="plus",
                ),
            )
        },
    )

    controller = AccountController(AppPaths.from_home(home), cache=cache)
    controller.reload()
    controller.set_usage_fetcher(lambda *_: (_ for _ in ()).throw(requests.Timeout("network down")))

    controller.refresh_selected()

    assert controller.status_message is not None
    assert "network down" in controller.status_message
    assert controller.cache.account_status_by_profile["work"] == "deactivated"
    assert controller.selected_row.account_status == "deactivated"
    assert controller.selected_row.usage is not None
    assert controller.selected_row.usage.usage.email == "alpha@example.com"


def test_controller_refreshes_all_profiles_even_if_one_fails(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "alpha.json").write_text(
        '{"tokens":{"account_id":"acct-alpha","access_token":"tok-alpha"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "beta.json").write_text(
        '{"tokens":{"account_id":"acct-beta","access_token":"tok-beta"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.set_usage_fetcher(
        lambda account_id, access_token: (
            (_ for _ in ()).throw(requests.Timeout("alpha down"))
            if account_id == "acct-alpha"
            else UsageResponse(
                email="beta@example.com",
                plan_type="plus",
                rate_limit=UsageRateLimit(
                    secondary_window=UsageWindow(
                        used_percent=44.0,
                        limit_window_seconds=604800,
                        reset_after_seconds=7200,
                        reset_at=604800,
                    )
                ),
            )
        )
    )

    failures = controller.refresh_all_profiles()

    assert failures == 1
    assert "alpha" not in controller.cache.usage_by_profile
    assert controller.cache.usage_by_profile["beta"].usage.email == "beta@example.com"


def test_controller_treats_deactivated_refresh_as_handled_in_bulk_refresh(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "alpha.json").write_text(
        '{"tokens":{"account_id":"acct-alpha","access_token":"tok-alpha"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "beta.json").write_text(
        '{"tokens":{"account_id":"acct-beta","access_token":"tok-beta"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.set_usage_fetcher(
        lambda account_id, access_token: (
            (_ for _ in ()).throw(
                UsageRefreshError(
                    status_code=401,
                    error_code="account_deactivated",
                    response_text="account_deactivated",
                )
            )
            if account_id == "acct-alpha"
            else UsageResponse(
                email="beta@example.com",
                plan_type="plus",
                rate_limit=UsageRateLimit(
                    secondary_window=UsageWindow(
                        used_percent=44.0,
                        limit_window_seconds=604800,
                        reset_after_seconds=7200,
                        reset_at=604800,
                    )
                ),
            )
        )
    )

    failures = controller.refresh_all_profiles()

    assert failures == 0
    assert controller.cache.account_status_by_profile["alpha"] == "deactivated"
    assert controller.cache.usage_by_profile["beta"].usage.email == "beta@example.com"


def test_controller_reports_usage_refresh_failures(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    controller = AccountController(AppPaths.from_home(home))
    controller.reload()
    controller.move_selection(1)
    controller.set_usage_fetcher(lambda *_: (_ for _ in ()).throw(requests.Timeout("network down")))

    controller.refresh_selected()

    assert controller.selected_row.usage is None
    assert controller.status_message is not None
    assert "network down" in controller.status_message
