# DeskFlow

A terminal-native, keyboard-driven **Pomodoro timer** and **Markdown checklist manager** built with [Textual](https://github.com/Textualize/textual).

## Features

- 📋 **Live Markdown task management** — edit your `.md` file in any editor while DeskFlow syncs changes in real time
- 🍅 **Drift-free Pomodoro timer** — epoch-based countdown that never drifts, even under heavy UI load
- ⚔️ **Conflict detection** — detects external edits and prompts you to resolve conflicts gracefully
- ⌨️ **Keyboard-first** — full keyboard control with visible keybinding help
- 🖱️ **Mouse support** — click checkboxes to toggle tasks
- 🔔 **Notifications** — terminal bell + desktop notifications at session end

## Installation

```bash
# Clone and install with uv
git clone <repo>
cd deskflow
uv sync
```

## Usage

```bash
# Launch with a Markdown file
uv run deskflow todo.md

# Or if installed globally
deskflow todo.md
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `s` | Start / Resume timer |
| `p` | Pause timer |
| `r` | Reset current session |
| `q` | Quit |
| `Space` | Toggle focused task |
| `↑` / `↓` | Navigate tasks |

## Configuration

Config file: `~/.config/deskflow/config.toml`

```toml
[timer]
work_minutes = 25
short_break_minutes = 5
long_break_minutes = 15
pomodoros_before_long_break = 4

[notifications]
bell = true
desktop = true
```

## Project Structure

```
src/deskflow/
├── main.py      # CLI entrypoint
├── app.py       # Textual TUI application
├── parser.py    # Markdown state-machine parser
├── timer.py     # Drift-free Pomodoro engine
├── config.py    # Configuration loader
├── notifier.py  # Cross-platform notifications
└── tcss/
    └── style.tcss  # Declarative styles
```

## Running Tests

```bash
uv run pytest
```
