from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable

from requests import RequestException

from codex_switch.cache import AppCache, load_cache, save_cache
from codex_switch.config import AppPaths
from codex_switch.repository import AccountRepository
from codex_switch.usage import (
    UsageRefreshError,
    UsageResponse,
    UsageSnapshot,
    fetch_usage,
    parse_usage_response,
)


@dataclass(slots=True)
class ProfileRow:
    key: str
    name: str
    is_unsaved: bool
    is_current: bool
    snapshot: dict[str, Any]
    usage: UsageSnapshot | None = None
    account_status: str = "active"


@dataclass(slots=True)
class DialogState:
    kind: str
    target_name: str | None = None


class AccountController:
    def __init__(
        self,
        paths: AppPaths,
        *,
        cache: AppCache | None = None,
        usage_fetcher: Callable[[str, str], UsageResponse] = fetch_usage,
        now_seconds: Callable[[], int] | None = None,
    ) -> None:
        self.paths = paths
        self.cache = cache or load_cache(paths.cache_path)
        self.repository = AccountRepository(paths, self.cache)
        self.usage_fetcher = usage_fetcher
        self._now_seconds = now_seconds or (lambda: int(time.time()))
        self.rows: list[ProfileRow] = []
        self.selected_index = 0
        self.dialog: DialogState | None = None
        self.status_message: str | None = None

    def set_usage_fetcher(self, usage_fetcher: Callable[[str, str], UsageResponse]) -> None:
        self.usage_fetcher = usage_fetcher

    def reload(self) -> None:
        saved_profiles = self.repository.list_saved_profiles()
        unsaved_profiles = self.repository.list_unsaved_profiles()
        current_snapshot = self.repository.read_current_snapshot()
        current_name = self.repository.current_profile_name()

        rows: list[ProfileRow] = []
        if current_name is None and current_snapshot is not None:
            rows.append(
                ProfileRow(
                    key="__current__",
                    name="Current (unsaved)",
                    is_unsaved=True,
                    is_current=True,
                    snapshot=current_snapshot,
                    usage=self.cache.usage_by_profile.get("__current__"),
                    account_status=self.cache.account_status_by_profile.get("__current__", "active"),
                )
            )

        for profile in saved_profiles:
            snapshot = json.loads(profile.path.read_text(encoding="utf-8"))
            rows.append(
                ProfileRow(
                    key=profile.name,
                    name=profile.name,
                    is_unsaved=False,
                    is_current=profile.name == current_name,
                    snapshot=snapshot,
                    usage=self.cache.usage_by_profile.get(profile.name),
                    account_status=self.cache.account_status_by_profile.get(profile.name, "active"),
                )
            )

        for profile in unsaved_profiles:
            snapshot = json.loads(profile.path.read_text(encoding="utf-8"))
            rows.append(
                ProfileRow(
                    key=profile.name,
                    name=profile.name,
                    is_unsaved=True,
                    is_current=False,
                    snapshot=snapshot,
                    usage=self.cache.usage_by_profile.get(profile.name),
                    account_status=self.cache.account_status_by_profile.get(profile.name, "active"),
                )
            )

        self.rows = rows
        self.selected_index = self._choose_selected_index(current_name)
        self._persist_selection()

    def move_selection(self, delta: int) -> None:
        if not self.rows:
            return
        self.selected_index = (self.selected_index + delta) % len(self.rows)
        self._persist_selection()

    def enter(self) -> None:
        row = self.selected_row
        if row is None:
            return
        if row.is_unsaved:
            self.dialog = DialogState(kind="save")
            return
        try:
            self.repository.switch_to_profile(row.name)
        except (FileNotFoundError, ValueError) as exc:
            self.status_message = str(exc)
            return
        self.status_message = None
        self.reload()

    def request_rename(self) -> None:
        row = self.selected_row
        if row is None or row.is_unsaved:
            return
        self.dialog = DialogState(kind="rename", target_name=row.name)

    def request_delete(self) -> None:
        row = self.selected_row
        if row is None or row.is_unsaved:
            return
        self.dialog = DialogState(kind="delete", target_name=row.name)

    def submit_dialog(self, value: str) -> None:
        if self.dialog is None:
            return
        kind = self.dialog.kind
        try:
            if kind == "save":
                row = self.selected_row
                if row is not None and row.is_unsaved and row.key != "__current__":
                    self.repository.save_unsaved_profile(row.key, value)
                else:
                    self.repository.save_current_profile(value)
            elif kind == "rename":
                if self.dialog.target_name is None:
                    return
                self.repository.rename_profile(self.dialog.target_name, value)
            else:
                return
        except (FileExistsError, FileNotFoundError, ValueError) as exc:
            self.status_message = str(exc)
            return
        self.dialog = None
        self.status_message = None
        self.reload()

    def confirm_delete(self, confirmed: bool) -> None:
        if self.dialog is None or self.dialog.kind != "delete":
            return
        target = self.dialog.target_name
        self.dialog = None
        if confirmed and target is not None:
            try:
                self.repository.delete_profile(target)
            except (FileNotFoundError, ValueError) as exc:
                self.status_message = str(exc)
                return
            self.status_message = None
            self.reload()

    def refresh_selected(self) -> None:
        row = self.selected_row
        if row is None:
            return
        try:
            handled = self._refresh_profile_usage(row)
        except (RequestException, TypeError, ValueError) as exc:
            self.status_message = f"failed to refresh usage for {row.name}: {exc}"
            return
        if handled:
            self.status_message = None
        self.reload()

    def refresh_all_visible(self) -> None:
        failures = self.refresh_all_profiles()
        if failures:
            self.status_message = f"failed to refresh usage for {failures} profile(s)"
        else:
            self.status_message = None
        self.reload()

    def refresh_all_profiles(self) -> int:
        failures = 0
        for row in list(self.rows):
            try:
                handled = self._refresh_profile_usage(row)
            except (RequestException, TypeError, ValueError):
                failures += 1
                continue
            if not handled:
                continue
        return failures

    @property
    def selected_row(self) -> ProfileRow | None:
        if not self.rows:
            return None
        return self.rows[self.selected_index]

    def current_profile_name(self) -> str | None:
        return self.repository.current_profile_name()

    def now_seconds(self) -> int:
        return self._now_seconds()

    def _choose_selected_index(self, current_name: str | None) -> int:
        selected_key = self.cache.selected_profile
        if selected_key:
            for index, row in enumerate(self.rows):
                if row.key == selected_key:
                    return index
        if current_name is not None:
            for index, row in enumerate(self.rows):
                if row.key == current_name:
                    return index
        if self.rows and self.rows[0].is_unsaved:
            return 0
        return 0

    def _persist_selection(self) -> None:
        row = self.selected_row
        self.cache.selected_profile = None if row is None else row.key
        save_cache(self.paths.cache_path, self.cache)

    def _credentials_from_snapshot(self, snapshot: dict[str, Any]) -> tuple[str | None, str | None]:
        tokens = snapshot.get("tokens") or {}
        account_id = tokens.get("account_id")
        access_token = tokens.get("access_token")
        return (
            account_id if isinstance(account_id, str) and account_id.strip() else None,
            access_token if isinstance(access_token, str) and access_token.strip() else None,
        )

    def _coerce_usage(self, usage: Any) -> UsageResponse:
        if isinstance(usage, UsageResponse):
            return usage
        if isinstance(usage, dict):
            return parse_usage_response(usage)
        raise TypeError(f"unexpected usage payload: {type(usage)!r}")

    def _store_usage_snapshot(self, row: ProfileRow, usage: UsageResponse) -> None:
        self.cache.usage_by_profile[row.key] = UsageSnapshot(
            fetched_at=self._now_seconds(),
            usage=usage,
        )
        row.usage = self.cache.usage_by_profile[row.key]

    def _refresh_profile_usage(self, row: ProfileRow) -> bool:
        account_id, access_token = self._credentials_from_snapshot(row.snapshot)
        if not account_id or not access_token:
            return False
        try:
            usage = self._coerce_usage(self.usage_fetcher(account_id, access_token))
        except UsageRefreshError:
            self.cache.account_status_by_profile[row.key] = "deactivated"
            row.account_status = "deactivated"
            save_cache(self.paths.cache_path, self.cache)
            return True
        self._store_usage_snapshot(row, usage)
        self.cache.account_status_by_profile.pop(row.key, None)
        row.account_status = "active"
        save_cache(self.paths.cache_path, self.cache)
        return True
