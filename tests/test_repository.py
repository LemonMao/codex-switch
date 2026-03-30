import json

import pytest

from codex_switch.cache import AppCache, load_cache, save_cache
from codex_switch.config import AppPaths
from codex_switch.repository import AccountRepository
from codex_switch.usage import UsageRateLimit, UsageResponse, UsageSnapshot, UsageWindow


def test_repository_manages_current_profile_and_snapshots(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "alpha.json").write_text(
        '{"tokens":{"account_id":"acct-alpha","access_token":"tok-alpha"}}\n',
        encoding="utf-8",
    )

    paths = AppPaths.from_home(home)
    assert paths.cache_path == paths.root_dir / "codex_switch_cache.json"
    repo = AccountRepository(paths)

    assert [profile.name for profile in repo.list_saved_profiles()] == ["alpha", "work"]
    assert repo.read_current_snapshot()["tokens"]["account_id"] == "acct-current"
    assert repo.current_profile_name() is None

    repo.save_current_profile("saved-current")
    assert repo.current_profile_name() == "saved-current"
    assert paths.auth_path.is_symlink()
    assert json.loads((snapshots_dir / "saved-current.json").read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-current"

    repo.switch_to_profile("work")
    assert repo.current_profile_name() == "work"

    repo.rename_profile("work", "office")
    assert repo.current_profile_name() == "office"
    assert not (snapshots_dir / "work.json").exists()
    assert (snapshots_dir / "office.json").exists()

    repo.delete_profile("office")
    assert not (snapshots_dir / "office.json").exists()
    assert not paths.auth_path.is_symlink()
    assert repo.current_profile_name() is None
    assert repo.read_current_snapshot()["tokens"]["account_id"] == "acct-work"


def test_repository_backs_up_unsaved_current_profile_before_switching_to_saved_profile(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    root_dir = codex_dir / "myaccounts"
    snapshots_dir = root_dir / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-unsaved","access_token":"tok-unsaved"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    repo = AccountRepository(AppPaths.from_home(home), now_ns=lambda: 1234567890)

    repo.switch_to_profile("work")

    backup_path = root_dir / "unsaved_profile_1234567890.json"
    assert backup_path.exists()
    assert json.loads(backup_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-unsaved"
    assert repo.current_profile_name() == "work"
    assert repo.paths.auth_path.is_symlink()


def test_repository_promotes_unsaved_backup_into_snapshots_and_deletes_source(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    root_dir = codex_dir / "myaccounts"
    snapshots_dir = root_dir / "snapshots"
    snapshots_dir.mkdir(parents=True)
    unsaved_path = root_dir / "unsaved_profile_1234567890.json"
    unsaved_path.parent.mkdir(parents=True, exist_ok=True)
    unsaved_path.write_text(
        '{"tokens":{"account_id":"acct-unsaved","access_token":"tok-unsaved"}}\n',
        encoding="utf-8",
    )

    repo = AccountRepository(AppPaths.from_home(home), now_ns=lambda: 1234567890)

    repo.save_unsaved_profile("unsaved_profile_1234567890", "saved-current")

    saved_path = snapshots_dir / "saved-current.json"
    assert saved_path.exists()
    assert json.loads(saved_path.read_text(encoding="utf-8"))["tokens"]["account_id"] == "acct-unsaved"
    assert not unsaved_path.exists()


def test_repository_does_not_switch_when_unsaved_backup_write_fails(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-unsaved","access_token":"tok-unsaved"}}\n',
        encoding="utf-8",
    )
    (snapshots_dir / "work.json").write_text(
        '{"tokens":{"account_id":"acct-work","access_token":"tok-work"}}\n',
        encoding="utf-8",
    )

    repo = AccountRepository(AppPaths.from_home(home), now_ns=lambda: 1234567890)

    def fail_write_json(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(repo, "_write_json", fail_write_json)

    with pytest.raises(OSError, match="disk full"):
        repo.switch_to_profile("work")

    assert not repo.paths.auth_path.is_symlink()
    assert repo.read_current_snapshot()["tokens"]["account_id"] == "acct-unsaved"


def test_repository_rejects_profile_names_that_escape_snapshot_dir(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    repo = AccountRepository(AppPaths.from_home(home))

    with pytest.raises(ValueError, match="invalid profile name"):
        repo.save_current_profile("../../evil")

    assert not (codex_dir / "evil.json").exists()


def test_cache_round_trip_preserves_usage_snapshot(tmp_path):
    path = tmp_path / "cache.json"
    cache = AppCache(
        selected_profile="work",
        account_status_by_profile={"work": "deactivated"},
        usage_by_profile={
            "work": UsageSnapshot(
                fetched_at=123,
                usage=UsageResponse(
                    email="alpha@example.com",
                    plan_type="plus",
                    rate_limit=UsageRateLimit(
                        secondary_window=UsageWindow(
                            used_percent=50.0,
                            limit_window_seconds=604_800,
                            reset_after_seconds=12_000,
                            reset_at=604_800,
                        )
                    ),
                ),
            )
        },
    )

    save_cache(path, cache)
    loaded = load_cache(path)

    assert loaded.selected_profile == "work"
    assert loaded.account_status_by_profile["work"] == "deactivated"
    assert loaded.usage_by_profile["work"].fetched_at == 123
    assert loaded.usage_by_profile["work"].usage.email == "alpha@example.com"


def test_cache_loads_cache_without_account_status_mapping(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "selected_profile": "work",
                "usage_by_profile": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_cache(path)

    assert loaded.selected_profile == "work"
    assert loaded.account_status_by_profile == {}


def test_repository_migrates_account_status_independently_of_usage(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    snapshots_dir = codex_dir / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (codex_dir / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (codex_dir / "auth.json").write_text(
        '{"tokens":{"account_id":"acct-current","access_token":"tok-current"}}\n',
        encoding="utf-8",
    )

    paths = AppPaths.from_home(home)
    cache = AppCache(account_status_by_profile={"__current__": "deactivated"})
    repo = AccountRepository(paths, cache)

    repo.save_current_profile("saved-current")
    assert repo.cache.account_status_by_profile["saved-current"] == "deactivated"
    assert "__current__" not in repo.cache.account_status_by_profile

    repo.rename_profile("saved-current", "renamed-current")
    assert repo.cache.account_status_by_profile["renamed-current"] == "deactivated"
    assert "saved-current" not in repo.cache.account_status_by_profile

    repo.delete_profile("renamed-current")
    assert repo.cache.account_status_by_profile == {}
    assert not repo.paths.auth_path.is_symlink()
    assert repo.current_profile_name() is None
    assert repo.read_current_snapshot()["tokens"]["account_id"] == "acct-current"
