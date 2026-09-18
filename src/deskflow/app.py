"""DeskFlow TUI Application.

Primary Textual App orchestrating the two-panel layout:
  - Left panel:  Markdown task list (SelectionList) backed by file sync
  - Right panel: Pomodoro timer (Digits + controls)

Implements Risk A mitigation: watchdog-based file watcher with SHA-256
conflict detection and a ConflictModal for merge resolution.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Digits,
    Footer,
    Label,
    SelectionList,
    Static,
)
from textual.widgets.selection_list import Selection
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from deskflow.config import DeskFlowConfig, load_config
from deskflow.notifier import notify
from deskflow.parser import (
    ParsedDocument,
    Task,
    build_display_lines,
    compute_sha256,
    parse_markdown,
    write_task_state,
    LineInfo,
)
from deskflow.timer import PomodoroTimer, SessionType, TimerState


# ── File watcher ──────────────────────────────────────────────────────────────


class _MarkdownWatchHandler(FileSystemEventHandler):
    """Watchdog handler that signals the app when the target file changes."""

    def __init__(self, target: Path, callback: object) -> None:
        super().__init__()
        self._target = str(target.resolve())
        self._callback = callback  # thread-safe callable

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path) == self._target:
            self._callback()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path) == self._target:
            self._callback()


# ── Custom Selection List ─────────────────────────────────────────────────────

from textual.strip import Strip

class TaskSelectionList(SelectionList[str]):
    """A customized SelectionList that hides checkboxes for disabled items (headings)."""

    def render_line(self, y: int) -> Strip:
        _, scroll_y = self.scroll_offset
        selection_index = scroll_y + y
        
        try:
            selection = self.get_option_at_index(selection_index)
            if getattr(selection, "disabled", False):
                # Return the OptionList's render_line directly, bypassing SelectionList's checkbox injection
                return super(SelectionList, self).render_line(y)
        except Exception:
            pass
            
        return super().render_line(y)


# ── Conflict modal ────────────────────────────────────────────────────────────


class ConflictModal(ModalScreen[bool]):
    """Modal dialog displayed when an external editor modifies the target file.

    Returns:
        True  → Keep TUI state (overwrite the file with TUI state)
        False → Reload from file (lose any unsaved TUI changes)
    """

    DEFAULT_CSS = ""  # Styles loaded from style.tcss

    def compose(self) -> ComposeResult:
        with Container(id="conflict-dialog"):
            yield Label("⚠  File Conflict Detected", id="conflict-title")
            yield Label(
                "The Markdown file was modified by an external editor.\n"
                "What would you like to do?",
                id="conflict-body",
            )
            with Horizontal(id="conflict-buttons"):
                yield Button(
                    "Keep TUI → Overwrite File",
                    id="btn-keep-tui",
                    variant="warning",
                )
                yield Button(
                    "Reload File → Lose TUI Changes",
                    id="btn-reload-file",
                    variant="error",
                )

    @on(Button.Pressed, "#btn-keep-tui")
    def keep_tui(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-reload-file")
    def reload_file(self) -> None:
        self.dismiss(False)


# ── Main application ──────────────────────────────────────────────────────────


class DeskFlowApp(App[None]):
    """Terminal-native Markdown-driven Pomodoro and checklist manager."""

    CSS_PATH = Path(__file__).parent / "tcss" / "style.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("s", "start_timer", "Start/Resume", show=True),
        Binding("p", "pause_timer", "Pause", show=True),
        Binding("r", "reset_timer", "Reset", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, target_file: Path, config: DeskFlowConfig | None = None) -> None:
        super().__init__()
        self._target_file = target_file.resolve()
        self._config = config or load_config()
        self._doc: ParsedDocument | None = None
        self._display_lines: list[LineInfo] = []
        self._known_sha: str = ""
        self._has_local_changes: bool = False
        self._conflict_pending: bool = False

        # Timer
        self._timer = PomodoroTimer(config=self._config)
        self._timer.on_tick(self._on_timer_tick)
        self._timer.on_complete(self._on_timer_complete)

        # Watchdog
        self._observer: Observer | None = None

    # ── Compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Build the two-panel layout."""
        # Compute initial timer display string from config
        cfg = self._config
        work_secs = int(cfg.work_seconds)
        initial_time = f"{work_secs // 60:02d}:{work_secs % 60:02d}"
        max_p = cfg.timer.pomodoros_before_long_break
        initial_dots = "○ " * max_p
        initial_dots = initial_dots.strip() + f"  (0/{max_p})"

        with Container(id="left-panel"):
            yield Label("📋  DeskFlow", id="task-list-title")
            yield Label(
                f"📄  {self._target_file.name}", id="file-label"
            )
            yield TaskSelectionList(id="task-list")
            yield Label("", id="progress-bar")

        with Container(id="right-panel"):
            yield Label("🍅  Pomodoro Timer", id="timer-title")
            yield Label(f"🍅  WORK SESSION", id="session-label")
            yield Label(initial_dots, id="dot-progress")
            yield Digits(initial_time, id="timer-digits")
            yield Label(f"Session 1 of {max_p} · Press [bold]s[/bold] to start", id="session-counter-label")
            with Horizontal(classes="button-row"):
                yield Button("▶ Start", id="btn-start", variant="success")
                yield Button("⏸ Pause", id="btn-pause", variant="warning")
                yield Button("↺ Reset", id="btn-reset", variant="error")

        yield Static(
            "s start  │  p pause  │  r reset  │  q quit  │  space toggle task  │  ↑↓ navigate",
            id="help-bar",
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        """Load the Markdown file and start the file watcher on mount."""
        self._load_file()
        self._start_watcher()
        # Update timer display immediately
        self._refresh_timer_ui()

    def on_unmount(self) -> None:
        """Stop the file watcher and timer on exit."""
        self._stop_watcher()
        self._timer.reset()

    # ── File loading ──────────────────────────────────────────────────────────

    def _load_file(self) -> None:
        """Parse the target Markdown file and populate the task list."""
        try:
            self._doc = parse_markdown(self._target_file)
            self._known_sha = self._doc.sha256
            self._display_lines = build_display_lines(self._doc)
            self._has_local_changes = False
            self._populate_task_list()
        except OSError as exc:
            self.notify(
                f"Cannot read file: {exc}",
                title="File Error",
                severity="error",
            )

    def _populate_task_list(self) -> None:
        """Rebuild the SelectionList from *_display_lines*."""
        task_list = self.query_one("#task-list", TaskSelectionList)
        task_list.clear_options()

        for info in self._display_lines:
            if info.is_task and info.task is not None:
                t = info.task
                label = f"{'  ' * (len(t.indent) // 2)}{t.text}"
                task_list.add_option(
                    Selection(label, value=t.id, initial_state=t.checked)
                )
            else:
                # Non-task lines: shown as disabled separator items
                display = info.display_text
                if display.startswith("#"):
                    # Header — style with markup
                    clean_text = display.lstrip("#").strip()
                    label = f"[bold #bb9af7]{clean_text}[/bold #bb9af7]"
                elif display.startswith("```") or display.startswith("~~~"):
                    label = f"[dim]{display}[/dim]"
                else:
                    label = f"[dim italic]{display}[/dim italic]"
                from textual.content import Content
                task_list.add_option(Selection(Content.from_markup(label), value=f"__ro_{id(info)}", disabled=True))

        self._update_progress_bar()

    def _update_progress_bar(self) -> None:
        """Update the task completion progress label."""
        if self._doc is None:
            return
        total = self._doc.task_count
        done = self._doc.checked_count
        pct = int((done / total * 100) if total > 0 else 0)
        bar = self.query_one("#progress-bar", Label)
        bar.update(f"[#9ece6a]Tasks: {done}/{total} ({pct}%)[/#9ece6a]")

    # ── Task toggling ─────────────────────────────────────────────────────────

    @on(TaskSelectionList.SelectedChanged)
    def on_selection_changed(self, event: TaskSelectionList.SelectedChanged) -> None:
        """Write task state change back to the Markdown file immediately."""
        if self._doc is None:
            return

        task_list = self.query_one("#task-list", TaskSelectionList)

        # Build a map of task id → Task for quick lookup
        task_map: dict[str, Task] = {t.id: t for t in self._doc.tasks}

        # Walk all options and sync checked state
        for option in task_list.options:
            val = str(option.value)
            if val.startswith("__ro_"):
                continue
            if val in task_map:
                task = task_map[val]
                is_checked = option.value in task_list.selected
                if is_checked != task.checked:
                    try:
                        new_sha_bytes = write_task_state(
                            self._target_file, task, is_checked
                        )
                        self._known_sha = new_sha_bytes.decode()
                        # Update our in-memory doc state
                        task.checked = is_checked
                        self._has_local_changes = True
                        self._update_progress_bar()
                    except (OSError, ValueError) as exc:
                        self.notify(
                            f"Write failed: {exc}",
                            title="File Error",
                            severity="error",
                        )

    # ── Watchdog file watcher ─────────────────────────────────────────────────

    def _start_watcher(self) -> None:
        """Start the watchdog observer in a background thread."""
        handler = _MarkdownWatchHandler(
            self._target_file, self._on_file_changed_thread_safe
        )
        self._observer = Observer()
        self._observer.schedule(
            handler, str(self._target_file.parent), recursive=False
        )
        self._observer.start()

    def _stop_watcher(self) -> None:
        """Stop the watchdog observer."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def _on_file_changed_thread_safe(self) -> None:
        """Called from the watchdog thread; schedule work on the Textual event loop."""
        # Use call_from_thread to safely invoke async code from the watcher thread
        try:
            self.call_from_thread(self._handle_file_change)
        except Exception:
            pass  # App may be shutting down

    def _handle_file_change(self) -> None:
        """Handle an external file modification (runs on the Textual event loop)."""
        if self._conflict_pending:
            return  # Already showing a conflict dialog

        try:
            current_content = self._target_file.read_bytes()
        except OSError:
            return

        current_sha = compute_sha256(current_content)
        if current_sha == self._known_sha:
            return  # No real change (our own write triggered the event)

        if self._has_local_changes:
            # Conflict: user has unsaved TUI changes AND the file changed externally
            self._conflict_pending = True
            self.push_screen(ConflictModal(), self._on_conflict_resolved)
        else:
            # No local changes: silently reload
            self._silent_reload()

    def _silent_reload(self) -> None:
        """Reload the file without prompting the user."""
        self._load_file()
        self.notify("File reloaded from disk.", title="DeskFlow", severity="information")

    def _on_conflict_resolved(self, keep_tui: bool) -> None:
        """Called when the user dismisses the ConflictModal.

        Args:
            keep_tui: True → write current TUI task states back to file.
                      False → reload from the external file.
        """
        self._conflict_pending = False
        if keep_tui:
            # Re-write each task's current state to overwrite the external changes
            if self._doc:
                for task in self._doc.tasks:
                    try:
                        new_sha_bytes = write_task_state(
                            self._target_file, task, task.checked
                        )
                        self._known_sha = new_sha_bytes.decode()
                    except (OSError, ValueError):
                        pass
            self.notify(
                "TUI state written to file.", title="DeskFlow", severity="warning"
            )
        else:
            self._silent_reload()

    # ── Timer callbacks ───────────────────────────────────────────────────────

    async def _on_timer_tick(self, remaining: float) -> None:
        """Update the Digits widget on each timer tick."""
        self._refresh_timer_ui()

    async def _on_timer_complete(self, session: SessionType) -> None:
        """Handle session completion: notify and update UI."""
        notify(
            title=f"DeskFlow — {session.label} Complete!",
            message=f"Time to {('take a break' if session == SessionType.WORK else 'get back to work')}!",
            config=self._config.notifications,
        )
        self.notify(
            f"{session.emoji}  {session.label} complete!",
            title="DeskFlow",
            severity="information",
        )
        self._refresh_timer_ui()

    def _refresh_timer_ui(self) -> None:
        """Update all timer-related widgets from current timer state."""
        try:
            digits = self.query_one("#timer-digits", Digits)
            session_label = self.query_one("#session-label", Label)
            dot_label = self.query_one("#dot-progress", Label)
            counter_label = self.query_one("#session-counter-label", Label)

            digits.update(self._timer.remaining_fmt())

            session = self._timer.session_type
            session_label.update(f"{session.emoji}  {session.label}")
            dot_label.update(self._timer.dot_progress())

            # Update session counter hint
            max_p = self._config.timer.pomodoros_before_long_break
            count = self._timer.pomodoro_count
            if self._timer.is_running:
                next_n = count + 1
                counter_label.update(f"Pomodoro {next_n} of {max_p}")
            elif self._timer.is_paused:
                counter_label.update("⏸  Paused")
            else:
                next_n = count + 1
                state_str = "Press [bold]s[/bold] to start"
                counter_label.update(f"Session {next_n} of {max_p} · {state_str}")
        except Exception:
            pass  # Widgets may not be mounted yet

    # ── Key actions ───────────────────────────────────────────────────────────

    def action_start_timer(self) -> None:
        """Start or resume the Pomodoro timer."""
        self._timer.start()
        self._refresh_timer_ui()
        self.notify("Timer started.", title="DeskFlow", severity="information")

    def action_pause_timer(self) -> None:
        """Pause the Pomodoro timer."""
        self._timer.pause()
        self._refresh_timer_ui()
        self.notify("Timer paused.", title="DeskFlow", severity="warning")

    def action_reset_timer(self) -> None:
        """Reset the Pomodoro timer."""
        self._timer.reset()
        self._refresh_timer_ui()
        self.notify("Timer reset.", title="DeskFlow", severity="warning")

    # ── Button handlers ───────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-start")
    def on_start_pressed(self) -> None:
        self.action_start_timer()

    @on(Button.Pressed, "#btn-pause")
    def on_pause_pressed(self) -> None:
        self.action_pause_timer()

    @on(Button.Pressed, "#btn-reset")
    def on_reset_pressed(self) -> None:
        self.action_reset_timer()
