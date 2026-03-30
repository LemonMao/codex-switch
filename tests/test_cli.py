from codex_switch.cli import run_import
from codex_switch.config import AppPaths


def test_run_import_reports_copied_profiles(tmp_path):
    home = tmp_path / "home"
    legacy_accounts = home / ".codex" / "accounts"
    legacy_accounts.mkdir(parents=True)
    (legacy_accounts / "alpha.json").write_text('{"token":"alpha"}\n', encoding="utf-8")
    (legacy_accounts / "beta.json").write_text('{"token":"beta"}\n', encoding="utf-8")

    paths = AppPaths.from_home(home)
    imported = run_import(paths)

    assert imported == ["alpha", "beta"]
    assert (paths.snapshots_dir / "alpha.json").exists()
    assert (paths.snapshots_dir / "beta.json").exists()
