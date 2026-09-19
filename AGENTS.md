# Agent Guidelines for DeskFlow

DeskFlow is a terminal-native, keyboard-driven Pomodoro timer and Markdown checklist manager built with Textual in Python 3.14 (managed via `uv`).

---

## 🛠️ Verification & Test Commands
All changes must be tested with pytest before submitting a PR:
- **Run All Tests**: `uv run --with pytest python -m pytest tests/` (all 57 tests must pass)
- **Run DeskFlow Locally**: `uv run deskflow todo.md`

---

## 🏛️ Codebase Architecture & Key Modules
- `src/deskflow/app.py`: Textual TUI application, key bindings, widget composition, screen transitions.
- `src/deskflow/timer.py`: Drift-free Pomodoro engine using monotonic epoch timestamps.
- `src/deskflow/parser.py`: Markdown state-machine parser handling checklist tasks, nesting, and external conflict detection.
- `src/deskflow/config.py`: Configuration loader (`~/.config/deskflow/config.toml`).
- `src/deskflow/notifier.py`: Cross-platform notifications (terminal bell + desktop notifications).
- `src/deskflow/tcss/style.tcss`: Textual CSS styles.
- `tests/test_parser.py`: Comprehensive parser validation suite.
- `tests/test_app.py`: Asynchronous TUI driver tests.

---

## 📋 Engineering Standards & Guardrails
1. **Drift-Free Precision**: Never replace epoch-based elapsed time calculations with frame-increment counters in `timer.py`.
2. **File Sync & Conflict Resilience**: Preserve external file-watch and conflict-resolution logic in `parser.py`.
3. **Async UI Responsiveness**: Avoid blocking calls inside Textual message handlers.
