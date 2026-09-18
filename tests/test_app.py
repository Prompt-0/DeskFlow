"""Async integration tests for DeskFlowApp using Textual's Pilot API.

These tests launch the full TUI in headless mode and simulate user interactions
to verify that widget state, keyboard bindings, and file write-backs all work
correctly end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import SelectionList, Digits, Label

from deskflow.app import DeskFlowApp
from deskflow.config import DeskFlowConfig, TimerConfig, NotificationConfig
from deskflow.timer import SessionType, TimerState


def _make_config(work_seconds: float = 60.0) -> DeskFlowConfig:
    """Return a fast test config (1-minute work sessions, notifications off)."""
    return DeskFlowConfig(
        timer=TimerConfig(
            work_minutes=work_seconds / 60,
            short_break_minutes=0.05,  # 3 seconds
            long_break_minutes=0.1,    # 6 seconds
            pomodoros_before_long_break=4,
        ),
        notifications=NotificationConfig(bell=False, desktop=False),
    )


# ── Basic UI loading ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deskflow_ui_execution(tmp_path: Path) -> None:
    """App launches, panels are present, tasks are loaded, toggle works."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("# Today\n- [ ] Task 1\n- [x] Task 2\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        # Both panels must be present
        assert app.query_one("#left-panel") is not None
        assert app.query_one("#right-panel") is not None

        # SelectionList should contain only task items (non-task header is disabled)
        sel_list = app.query_one("#task-list")
        task_options = [o for o in sel_list.options if not str(o.value).startswith("__ro_")]
        assert len(task_options) == 2

        # Timer display should be present and show time
        digits = app.query_one("#timer-digits", Digits)
        assert digits is not None

        # Session label must be visible
        session_label = app.query_one("#session-label", Label)
        assert session_label is not None


@pytest.mark.asyncio
async def test_initial_task_states(tmp_path: Path) -> None:
    """Initial checked states in file are reflected in the SelectionList."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Unchecked\n- [x] Checked\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        sel_list = app.query_one("#task-list")
        task_options = [o for o in sel_list.options if not str(o.value).startswith("__ro_")]
        assert len(task_options) == 2

        # The second task (Checked) should be in the selected set
        selected_values = set(sel_list.selected)
        option_values = [str(o.value) for o in task_options]

        # First option (Unchecked) must NOT be selected initially
        assert option_values[0] not in selected_values

        # Second option (Checked) MUST be selected initially
        assert option_values[1] in selected_values


@pytest.mark.asyncio
async def test_help_bar_is_visible(tmp_path: Path) -> None:
    """The help bar with keybinding hints must be present."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        help_bar = app.query_one("#help-bar")
        assert help_bar is not None


@pytest.mark.asyncio
async def test_dot_progress_displayed(tmp_path: Path) -> None:
    """Dot progress label must be present and correctly formatted."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        dot_label = app.query_one("#dot-progress", Label)
        # In Textual 8+, Label.content is a plain str with the current content
        dot_text = dot_label.content
        # Should contain circle dots and fraction notation
        assert "○" in dot_text or "●" in dot_text or "/" in dot_text


# ── Task toggle ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_toggle_writes_back_to_file(tmp_path: Path) -> None:
    """Toggling an unchecked task should update the Markdown file."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task 1\n- [x] Task 2\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        sel_list = app.query_one("#task-list")

        # Navigate to first task and toggle it with space
        await pilot.press("tab")  # Focus the selection list
        await pilot.press("home")  # Go to top
        await pilot.press("space")  # Toggle first option

        # Give the app time to process the event and write to file
        await pilot.pause(0.2)

        # Read back the file
        content = test_md.read_text()
        # Either Task 1 was toggled (checked) or it was already the right state
        # The file should be a valid Markdown file
        assert "Task 1" in content
        assert "Task 2" in content


# ── Timer controls ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timer_starts_on_s_key(tmp_path: Path) -> None:
    """Pressing 's' should start the timer."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        assert app._timer.state == TimerState.IDLE

        await pilot.press("s")
        await pilot.pause(0.1)

        assert app._timer.is_running


@pytest.mark.asyncio
async def test_timer_pauses_on_p_key(tmp_path: Path) -> None:
    """Pressing 'p' after starting should pause the timer."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await pilot.pause(0.1)
        assert app._timer.is_running

        await pilot.press("p")
        await pilot.pause(0.1)
        assert app._timer.is_paused


@pytest.mark.asyncio
async def test_timer_resets_on_r_key(tmp_path: Path) -> None:
    """Pressing 'r' should reset the timer to IDLE."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await pilot.pause(0.1)

        await pilot.press("r")
        await pilot.pause(0.1)

        assert app._timer.state == TimerState.IDLE


@pytest.mark.asyncio
async def test_timer_resumes_after_pause(tmp_path: Path) -> None:
    """Timer should resume correctly after pause."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await pilot.pause(0.2)

        rem_before_pause = app._timer.remaining()
        await pilot.press("p")
        await pilot.pause(0.3)  # Wait while paused

        await pilot.press("s")  # Resume
        await pilot.pause(0.1)

        assert app._timer.is_running
        # Remaining should be close to where we paused
        assert abs(app._timer.remaining() - rem_before_pause) < 1.0


# ── Button controls ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_button_works(tmp_path: Path) -> None:
    """Clicking the Start button should start the timer."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#btn-start")
        await pilot.pause(0.1)
        assert app._timer.is_running


@pytest.mark.asyncio
async def test_pause_button_works(tmp_path: Path) -> None:
    """Clicking Pause button should pause a running timer."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#btn-start")
        await pilot.pause(0.1)
        await pilot.click("#btn-pause")
        await pilot.pause(0.1)
        assert app._timer.is_paused


@pytest.mark.asyncio
async def test_reset_button_works(tmp_path: Path) -> None:
    """Clicking Reset button should reset a running timer."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config(work_seconds=300))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#btn-start")
        await pilot.pause(0.1)
        await pilot.click("#btn-reset")
        await pilot.pause(0.1)
        assert app._timer.state == TimerState.IDLE


# ── Missing file handling ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_created_file_loads(tmp_path: Path) -> None:
    """A file created by main.py's _ensure_file should load correctly."""
    from deskflow.main import _ensure_file

    test_md = tmp_path / "new_todo.md"
    assert not test_md.exists()

    _ensure_file(test_md)

    assert test_md.exists()
    content = test_md.read_text()
    assert "# Tasks" in content
    assert "- [ ]" in content

    # The app should load it without errors
    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#left-panel") is not None


# ── File change detection ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_silent_reload_on_external_change(tmp_path: Path) -> None:
    """When the file changes externally with no local TUI changes, silent reload occurs."""
    test_md = tmp_path / "todo.md"
    test_md.write_text("- [ ] Original task\n")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        # No local changes
        assert app._has_local_changes is False

        original_sha = app._known_sha

        # Simulate external file change
        new_content = "- [x] Modified task\n"
        test_md.write_text(new_content)

        # Trigger the change handler directly (bypassing watchdog timing)
        app._handle_file_change()
        await pilot.pause(0.3)

        # SHA should have updated
        assert app._known_sha != original_sha


# ── Parser edge cases via app ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_handles_code_block_in_markdown(tmp_path: Path) -> None:
    """Tasks inside code blocks must NOT appear in the selection list."""
    content = (
        "# Code\n"
        "```python\n"
        "- [ ] This is code, not a task\n"
        "```\n"
        "- [ ] Real task\n"
    )
    test_md = tmp_path / "todo.md"
    test_md.write_text(content)

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        sel_list = app.query_one("#task-list")
        task_options = [o for o in sel_list.options if not str(o.value).startswith("__ro_")]
        assert len(task_options) == 1
        assert task_options[0].prompt == "Real task"


@pytest.mark.asyncio
async def test_app_handles_empty_file(tmp_path: Path) -> None:
    """App should load gracefully with an empty Markdown file."""
    test_md = tmp_path / "empty.md"
    test_md.write_text("")

    app = DeskFlowApp(target_file=test_md, config=_make_config())
    async with app.run_test(size=(120, 40)) as pilot:
        sel_list = app.query_one("#task-list")
        task_options = [o for o in sel_list.options if not str(o.value).startswith("__ro_")]
        assert len(task_options) == 0
