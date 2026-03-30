from codex_switch.config import AppPaths
from codex_switch.repository import AccountRepository


def test_import_only_copies_legacy_accounts(tmp_path):
    home = tmp_path / "home"
    legacy_codex = home / ".codex"
    legacy_accounts = legacy_codex / "accounts"
    legacy_accounts.mkdir(parents=True)
    (legacy_accounts / "work.json").write_text('{"token":"work"}\n', encoding="utf-8")
    (legacy_accounts / "personal.json").write_text('{"token":"personal"}\n', encoding="utf-8")
    (legacy_codex / "auth.json").write_text('{"token":"active"}\n', encoding="utf-8")
    (legacy_codex / "current").write_text("should-not-be-imported\n", encoding="utf-8")

    paths = AppPaths.from_home(home)
    repo = AccountRepository(paths)

    imported = repo.import_legacy_accounts(legacy_accounts)

    assert imported == ["personal", "work"]
    assert (paths.snapshots_dir / "work.json").read_text(encoding="utf-8") == '{"token":"work"}\n'
    assert (paths.snapshots_dir / "personal.json").read_text(encoding="utf-8") == '{"token":"personal"}\n'
    assert not (paths.root_dir / "current").exists()
    assert not (paths.root_dir / "auth.json").exists()


def test_switch_to_profile_creates_auth_symlink(tmp_path):
    home = tmp_path / "home"
    snapshots_dir = home / ".codex" / "myaccounts" / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "work.json").write_text('{"token":"work"}\n', encoding="utf-8")

    paths = AppPaths.from_home(home)
    repo = AccountRepository(paths)

    repo.switch_to_profile("work")

    assert paths.auth_path.is_symlink()
    assert paths.auth_path.resolve() == snapshots_dir / "work.json"
