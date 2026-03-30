from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
from typing import Any

from codex_switch.cache import AppCache, load_cache, save_cache
from codex_switch.config import AppPaths
from codex_switch.usage import UsageSnapshot


@dataclass(slots=True)
class ProfileRecord:
    name: str
    path: Path


class AccountRepository:
    def __init__(self, paths: AppPaths, cache: AppCache | None = None) -> None:
        self.paths = paths
        self.cache = cache or load_cache(paths.cache_path)

    def list_saved_profiles(self) -> list[ProfileRecord]:
        if not self.paths.snapshots_dir.exists():
            return []
        profiles = [
            ProfileRecord(name=item.stem, path=item)
            for item in self.paths.snapshots_dir.glob("*.json")
            if item.is_file()
        ]
        return sorted(profiles, key=lambda profile: profile.name)

    def current_profile_name(self) -> str | None:
        if not self.paths.auth_path.is_symlink():
            return None
        try:
            resolved = self.paths.auth_path.resolve(strict=True)
        except FileNotFoundError:
            return None
        if self.paths.snapshots_dir not in resolved.parents:
            return None
        return resolved.stem

    def read_current_snapshot(self) -> dict[str, Any] | None:
        if not self.paths.auth_path.exists():
            return None
        return json.loads(self.paths.auth_path.read_text(encoding="utf-8"))

    def import_legacy_accounts(self, legacy_accounts_dir: Path) -> list[str]:
        self.paths.snapshots_dir.mkdir(parents=True, exist_ok=True)
        imported: list[str] = []
        for source in sorted(legacy_accounts_dir.glob("*.json")):
            if not source.is_file():
                continue
            destination = self.paths.snapshots_dir / source.name
            if destination.exists():
                continue
            shutil.copy2(source, destination)
            imported.append(source.stem)
        return imported

    def save_current_profile(self, name: str) -> str:
        destination = self._snapshot_path(name)
        if destination.exists():
            raise FileExistsError(f"saved profile already exists: {name}")
        snapshot = self._read_active_snapshot()
        self._ensure_dirs()
        self._write_json(destination, snapshot)
        self._link_auth_to_snapshot(destination)
        if "__current__" in self.cache.usage_by_profile:
            self.cache.usage_by_profile[name] = self.cache.usage_by_profile.pop("__current__")
        if "__current__" in self.cache.account_status_by_profile:
            self.cache.account_status_by_profile[name] = self.cache.account_status_by_profile.pop("__current__")
        self.cache.selected_profile = name
        self._persist_cache()
        return name

    def switch_to_profile(self, name: str) -> str:
        snapshot = self._snapshot_path(name)
        if not snapshot.exists():
            raise FileNotFoundError(f"saved profile not found: {name}")
        self._ensure_dirs()
        self._link_auth_to_snapshot(snapshot)
        self.cache.selected_profile = name
        self._persist_cache()
        return name

    def rename_profile(self, current_name: str, next_name: str) -> str:
        source = self._snapshot_path(current_name)
        destination = self._snapshot_path(next_name)
        if not source.exists():
            raise FileNotFoundError(f"saved profile not found: {current_name}")
        if current_name != next_name and destination.exists():
            raise FileExistsError(f"saved profile already exists: {next_name}")
        self._ensure_dirs()
        was_current = self.current_profile_name() == current_name
        if current_name != next_name:
            source.rename(destination)
        if was_current:
            self._link_auth_to_snapshot(destination)
        if current_name in self.cache.usage_by_profile:
            self.cache.usage_by_profile[next_name] = self.cache.usage_by_profile.pop(current_name)
        if current_name in self.cache.account_status_by_profile:
            self.cache.account_status_by_profile[next_name] = self.cache.account_status_by_profile.pop(current_name)
        if self.cache.selected_profile == current_name:
            self.cache.selected_profile = next_name
        self._persist_cache()
        return next_name

    def delete_profile(self, name: str) -> str:
        target = self._snapshot_path(name)
        if not target.exists():
            raise FileNotFoundError(f"saved profile not found: {name}")
        if self.current_profile_name() == name and self.paths.auth_path.is_symlink():
            snapshot = target.read_text(encoding="utf-8")
            self.paths.auth_path.unlink()
            self.paths.auth_path.write_text(snapshot, encoding="utf-8")
        target.unlink()
        self.cache.usage_by_profile.pop(name, None)
        self.cache.account_status_by_profile.pop(name, None)
        if self.cache.selected_profile == name:
            self.cache.selected_profile = None
        self._persist_cache()
        return name

    def store_usage_snapshot(self, profile_name: str, usage: Any, fetched_at: int) -> None:
        self.cache.usage_by_profile[profile_name] = UsageSnapshot(fetched_at=fetched_at, usage=usage)
        self._persist_cache()

    def usage_snapshot(self, profile_name: str) -> Any | None:
        snapshot = self.cache.usage_by_profile.get(profile_name)
        return None if snapshot is None else snapshot.usage

    def _ensure_dirs(self) -> None:
        self.paths.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.paths.root_dir.mkdir(parents=True, exist_ok=True)
        self.paths.codex_dir.mkdir(parents=True, exist_ok=True)

    def _persist_cache(self) -> None:
        save_cache(self.paths.cache_path, self.cache)

    def _snapshot_path(self, name: str) -> Path:
        self._validate_profile_name(name)
        return self.paths.snapshots_dir / f"{name}.json"

    def _validate_profile_name(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid profile name")

        candidate = Path(name)
        if candidate.is_absolute() or candidate.name != name:
            raise ValueError(f"invalid profile name: {name}")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError(f"invalid profile name: {name}")

    def _read_active_snapshot(self) -> dict[str, Any]:
        if not self.paths.auth_path.exists():
            raise FileNotFoundError(f"no Codex auth file found at {self.paths.auth_path}")
        return json.loads(self.paths.auth_path.read_text(encoding="utf-8"))

    def _link_auth_to_snapshot(self, snapshot: Path) -> None:
        if self.paths.auth_path.exists() or self.paths.auth_path.is_symlink():
            self.paths.auth_path.unlink()
        relative_target = os.path.relpath(snapshot, start=self.paths.codex_dir)
        os.symlink(relative_target, self.paths.auth_path)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
