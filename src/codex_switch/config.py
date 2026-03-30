from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True, slots=True)
class AppPaths:
    home_dir: Path
    codex_dir: Path
    root_dir: Path
    snapshots_dir: Path
    cache_path: Path
    auth_path: Path
    legacy_accounts_dir: Path

    @classmethod
    def from_home(cls, home_dir: Path | str) -> "AppPaths":
        home = Path(home_dir)
        codex_dir = home / ".codex"
        root_dir = codex_dir / "myaccounts"
        return cls(
            home_dir=home,
            codex_dir=codex_dir,
            root_dir=root_dir,
            snapshots_dir=root_dir / "snapshots",
            cache_path=root_dir / "codex_switch_cache.json",
            auth_path=codex_dir / "auth.json",
            legacy_accounts_dir=codex_dir / "accounts",
        )

    @classmethod
    def detect(cls) -> "AppPaths":
        override = os.environ.get("CODEX_SWITCH_HOME")
        home = Path(override) if override else Path.home()
        return cls.from_home(home)
