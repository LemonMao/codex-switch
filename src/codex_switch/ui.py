from __future__ import annotations

import asyncio
import hashlib

from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError
from textual.dom import NoScreen
from textual.containers import Horizontal
from textual.widgets import Footer, Input, Static

from codex_switch.controller import AccountController, DialogState, ProfileRow
from codex_switch.usage import (
    format_reset_eta,
    format_relative_age,
    pick_weekly_window,
    render_usage_bar,
)

PROFILE_COLORS = (
    "#84d6ff",
    "#de8df0",
    "#f2a0d8",
    "#8ed0a9",
    "#f2b86c",
    "#6fd3c0",
    "#f58f8f",
    "#7ed56f",
    "#ffb36b",
    "#b99cff",
    "#e6c26c",
    "#8fb8ff",
)

TOKYO_NIGHT_PALETTE = {
    "screen": "#1a1b26",
    "panel": "#24283b",
    "panel_alt": "#1f2335",
    "border": "#414868",
    "text": "#c0caf5",
    "muted": "#9aa5ce",
    "accent": "#7aa2f7",
    "cyan": "#7dcfff",
    "green": "#9ece6a",
    "yellow": "#e0af68",
    "red": "#f7768e",
    "magenta": "#bb9af7",
}

STATUS_SPINNER_FRAMES = ("|", "/", "-", "\\")
USAGE_REFRESH_INTERVAL_SECONDS = 300


class CodexSwitchApp(App[None]):
    AUTO_FOCUS = None
    theme = "tokyo-night"
    TOKYO_NIGHT = TOKYO_NIGHT_PALETTE
    STATUS_SPINNER_FRAMES = STATUS_SPINNER_FRAMES

    CSS = """
    Screen {
        background: $background;
        color: $foreground;
    }

    #main {
        height: 1fr;
    }

    #profiles {
        width: 42%;
        border: solid $border;
        padding: 1 1;
        background: $surface;
    }

    #details {
        width: 58%;
        border: solid $border;
        padding: 1 1;
        background: $panel;
    }

    #status {
        height: 4;
        border: solid $border;
        padding: 0 1;
        background: $panel;
    }

    #prompt {
        display: none;
        height: 3;
        border: solid $primary;
        padding: 0 1;
        background: $surface;
    }

    Footer {
        background: $surface;
        color: $foreground-muted;
    }
    """

    BINDINGS = [
        ("up", "move_up", "Up"),
        ("k", "move_up", "K"),
        ("down", "move_down", "Down"),
        ("j", "move_down", "J"),
        ("tab", "next_profile", "Tab"),
        ("enter", "select", "Enter"),
        ("n", "rename_profile", "Rename"),
        ("d", "delete_profile", "Delete"),
        ("u,U", "refresh_selected", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, controller: AccountController) -> None:
        super().__init__()
        self.controller = controller
        self._status_activity: str | None = None
        self._status_spinner_index = 0
        self._prompt_value: str = ""
        self._prompt_dialog_signature: tuple[str, str | None] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Static(id="profiles")
            yield Static(id="details")
        yield Static(id="status")
        yield Input(id="prompt", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.controller.reload()
        self._refresh_views()
        has_loop = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            has_loop = False
        if has_loop:
            self.set_interval(0.12, self._tick_status_spinner)
        self._start_background_refresh()
        if has_loop:
            self.set_interval(USAGE_REFRESH_INTERVAL_SECONDS, self._refresh_all_profiles_periodic)

    def on_key(self, event) -> None:  # type: ignore[override]
        if self.controller.dialog is None or event.key != "escape":
            return
        self.controller.dialog = None
        self._refresh_views()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.controller.dialog is None:
            return
        value = event.value.strip()
        kind = self.controller.dialog.kind
        if kind in {"save", "rename"}:
            if value:
                self.controller.submit_dialog(value)
        elif kind == "delete":
            self.controller.confirm_delete(value.lower() in {"y", "yes"})
        self._refresh_views()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.controller.dialog is None:
            return
        self._prompt_value = event.value

    def _refresh_views(self) -> None:
        profiles_widget = self.query_one("#profiles", Static)
        details_widget = self.query_one("#details", Static)
        status_widget = self.query_one("#status", Static)
        prompt_widget = self.query_one("#prompt", Input)

        border = self._theme_color("border")
        profiles_widget.update(Panel(self._render_profiles(), title="Profiles", border_style=border))
        details_widget.update(Panel(self._render_details(), title="Details", border_style=border))
        status_widget.update(Panel(self._render_status(), title="Status", border_style=border))

        if self.controller.dialog is None:
            self._prompt_dialog_signature = None
            self._prompt_value = ""
            self._hide_prompt()
        else:
            dialog_signature = (self.controller.dialog.kind, self.controller.dialog.target_name)
            if dialog_signature != self._prompt_dialog_signature:
                self._prompt_dialog_signature = dialog_signature
                self._prompt_value = self._dialog_default_value(self.controller.dialog)
            elif prompt_widget.value != self._prompt_value:
                self._prompt_value = prompt_widget.value
            prompt_widget.disabled = False
            prompt_widget.display = True
            if prompt_widget.value != self._prompt_value:
                prompt_widget.value = self._prompt_value
            prompt_widget.placeholder = self._prompt_placeholder(self.controller.dialog)
            prompt_widget.focus()

    def _start_background_refresh(self) -> None:
        self._begin_status_activity("Refreshing profile usage")
        self._refresh_views()
        self._schedule_background_worker(
            self._refresh_all_profiles_background,
            name="profile-usage-refresh",
            group="profile-usage",
        )

    def _refresh_all_profiles_background(self) -> None:
        try:
            failures = self.controller.refresh_all_profiles()
            if failures:
                self.controller.status_message = f"background refresh finished: {failures} failed"
            else:
                self.controller.status_message = None
        finally:
            self.call_from_thread(self._complete_background_activity)

    def _refresh_all_profiles_periodic(self) -> None:
        if self._status_activity is not None:
            return
        self._start_background_refresh()

    def _refresh_selected_background(self) -> None:
        try:
            self.controller.refresh_selected()
        finally:
            self.call_from_thread(self._complete_background_activity)

    def _hide_prompt(self) -> None:
        prompt_widget = self.query_one("#prompt", Input)
        try:
            self.screen.set_focus(None)
        except (NoScreen, ScreenStackError):
            pass
        prompt_widget.disabled = True
        prompt_widget.display = False
        prompt_widget.value = ""

    def _unsaved_profile_hint(self) -> str | None:
        row = self.controller.selected_row
        if row is None or not row.is_unsaved:
            return None
        seed = self._dialog_default_value(DialogState(kind="save"))
        if seed:
            return f"Found an unsaved profile; press Enter to save it as {seed}."
        return "Found an unsaved profile; press Enter to save it."

    def _render_profiles(self) -> Text:
        text = Text()
        rows = self.controller.rows
        if not rows:
            text.append("No saved profiles yet.\n", style=f"bold {self._theme_color('foreground')}")
            text.append("Run codex-switch import to load legacy snapshots.", style=self._theme_color("foreground-muted"))
            return text

        name_width = max(len(row.name) for row in rows)
        state_width = max(len(self._state_tag(row)) for row in rows)
        for index, row in enumerate(rows):
            text.append(self._render_profile_row(row, index, name_width, state_width))
            text.append("\n")
        return text

    def _render_details(self) -> Text:
        row = self.controller.selected_row
        text = Text()
        if row is None:
            text.append("No profile selected.", style=f"bold {self._theme_color('foreground')}")
            return text

        text.append("Profile: ", style=f"bold {self._theme_color('foreground')}")
        text.append(f"{row.name}\n", style=self._detail_profile_style(row.name))
        text.append("Service: Codex [cx]\n", style=self._theme_color("foreground"))
        text.append(f"State: {self._state_label(row)}\n", style=self._theme_color("foreground"))

        if row.usage is None:
            text.append("Last updated: unavailable\n", style=self._theme_color("foreground-muted"))
            text.append("Email: unavailable\n", style=self._theme_color("foreground-muted"))
            text.append("Plan: unavailable\n", style=self._theme_color("foreground-muted"))
            text.append("Weekly: unavailable\n", style=self._theme_color("foreground-muted"))
            return text

        usage = row.usage.usage
        fetched = row.usage.fetched_at
        weekly = pick_weekly_window(usage)

        text.append(f"Last updated: {format_relative_age(self.controller.now_seconds() - fetched)}\n", style=self._theme_color("foreground"))
        text.append(f"Email: {usage.email or 'unavailable'}\n", style=self._theme_color("foreground"))
        text.append(f"Plan: {usage.plan_type or 'unavailable'}\n", style=self._theme_color("foreground"))
        if weekly is not None:
            text.append(
                f"Weekly: {weekly.used_percent:.0f}% used, reset in {format_reset_eta(weekly.reset_after_seconds)}\n",
                style=self._theme_color("foreground"),
            )
        else:
            text.append("Weekly: unavailable\n", style=self._theme_color("foreground-muted"))
        return text

    def _render_status(self) -> Text:
        text = Text()
        if self._status_activity is not None:
            spinner = self.STATUS_SPINNER_FRAMES[self._status_spinner_index]
            text.append(f"{spinner} {self._status_activity}", style=f"bold {self._theme_color('secondary')}")
        elif self.controller.status_message:
            text.append(self.controller.status_message, style=f"bold {self._theme_color('error')}")
        elif (hint := self._unsaved_profile_hint()) is not None:
            text.append(hint, style=f"bold {self._theme_color('secondary')}")
        else:
            text.append("Ready", style=self._theme_color("foreground-muted"))
        text.append("\n")
        text.append(
            "j/k move | tab loop | enter save/switch | n rename | d delete | u/U refresh | q quit",
            style=self._theme_color("foreground-muted"),
        )
        return text

    def action_move_up(self) -> None:
        if self.controller.dialog is not None:
            return
        self.controller.move_selection(-1)
        self._refresh_views()

    def action_move_down(self) -> None:
        if self.controller.dialog is not None:
            return
        self.controller.move_selection(1)
        self._refresh_views()

    def action_next_profile(self) -> None:
        if self.controller.dialog is not None:
            return
        self.controller.move_selection(1)
        self._refresh_views()

    def action_select(self) -> None:
        if self.controller.dialog is not None:
            return
        self.controller.enter()
        self._refresh_views()

    def action_rename_profile(self) -> None:
        if self.controller.dialog is not None:
            return
        self.controller.request_rename()
        self._refresh_views()

    def action_delete_profile(self) -> None:
        if self.controller.dialog is not None:
            return
        self.controller.request_delete()
        self._refresh_views()

    def action_refresh_selected(self) -> None:
        if self.controller.dialog is not None:
            return
        self._begin_status_activity("Refreshing profile usage")
        self._refresh_views()
        self._schedule_background_worker(
            self._refresh_selected_background,
            name="profile-refresh",
            group="profile-refresh",
        )

    async def action_quit(self) -> None:
        if self.controller.dialog is not None:
            return
        self.exit()

    def _prompt_placeholder(self, dialog: DialogState) -> str:
        if dialog.kind == "save":
            return "Save current profile as..."
        if dialog.kind == "rename":
            return f"Rename {dialog.target_name or ''} to..."
        if dialog.kind == "delete":
            return "Type y to confirm delete"
        return "Input"

    def _dialog_default_value(self, dialog: DialogState) -> str:
        if dialog.kind != "save":
            return ""
        row = self.controller.selected_row
        if row is None or not row.is_unsaved or row.usage is None:
            return ""
        email = row.usage.usage.email
        if not email:
            return ""
        local_part = email.split("@", 1)[0].strip()
        return local_part if local_part else ""

    def _begin_status_activity(self, message: str) -> None:
        self.controller.status_message = None
        self._status_activity = message
        self._status_spinner_index = 0

    def _end_status_activity(self) -> None:
        self._status_activity = None

    def _complete_background_activity(self) -> None:
        self._end_status_activity()
        self._refresh_views()

    def _schedule_background_worker(self, work, *, name: str, group: str | None) -> None:
        self.call_after_refresh(
            self.run_worker,
            work,
            name=name,
            group=group,
            thread=True,
            exclusive=True,
        )

    def _tick_status_spinner(self) -> None:
        if self._status_activity is None:
            return
        self._status_spinner_index = (self._status_spinner_index + 1) % len(self.STATUS_SPINNER_FRAMES)
        self._refresh_views()

    def _render_profile_row(self, row: ProfileRow, index: int, name_width: int, state_width: int) -> Text:
        line = Text()
        selected = index == self.controller.selected_index
        name_style = self._profile_name_style(row.name, bold=selected)
        marker_style = name_style if selected else self._theme_color("foreground-muted")
        state_style = self._theme_color("foreground-muted")

        line.append("❯ " if selected else "  ", style=marker_style)
        line.append("* " if row.is_current else "  ", style=name_style if row.is_current else state_style)
        line.append(row.name.ljust(name_width), style=name_style)
        line.append(" ", style=state_style)
        line.append(self._state_tag(row).ljust(state_width), style=state_style)

        if row.usage and row.usage.usage.rate_limit:
            weekly = pick_weekly_window(row.usage.usage)
            if weekly is not None:
                line.append("  ")
                line.append(render_usage_bar(weekly.used_percent, width=16), style=name_style)
                line.append(
                    f" {weekly.used_percent:.1f}%, reset after {format_reset_eta(weekly.reset_after_seconds)}",
                    style=state_style,
                )
        return line

    def _detail_profile_style(self, name: str) -> str:
        return self._profile_name_style(name, bold=True)

    def _profile_name_style(self, name: str, *, bold: bool = False) -> str:
        accent = self._profile_color(name)
        return f"bold {accent}" if bold else accent

    def _theme_color(self, name: str) -> str:
        return self.get_css_variables()[name]

    def _profile_color(self, name: str) -> str:
        digest = hashlib.sha1(name.encode("utf-8")).digest()
        return PROFILE_COLORS[digest[0] % len(PROFILE_COLORS)]

    def _state_label(self, row: ProfileRow) -> str:
        if row.account_status == "deactivated":
            return "✕ deactivated"
        if row.is_unsaved:
            return "unsaved"
        if row.is_current:
            return "current"
        return "backup"

    def _state_tag(self, row: ProfileRow) -> str:
        label = self._state_label(row)
        if row.account_status == "deactivated":
            return label
        return f"[{label}]"
