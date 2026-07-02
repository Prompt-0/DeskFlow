"""DeskFlow CLI entrypoint.

Usage:
    deskflow todo.md

If the target Markdown file does not exist, it is created with a default
template so the user can get started immediately.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deskflow.config import load_config

_DEFAULT_TEMPLATE = """\
# Tasks

## Today
- [ ] My first task
- [ ] Review notes
- [ ] Plan tomorrow

## Work
- [ ] Write code
- [ ] Review pull requests
"""


def _ensure_file(path: Path) -> None:
    """Create *path* with the default template if it does not exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")
        print(f"Created {path} with default template.", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deskflow",
        description="Terminal-native Markdown-driven Pomodoro & checklist manager",
    )
    p.add_argument(
        "file",
        metavar="FILE",
        type=Path,
        help="Path to the Markdown todo file (created if missing)",
    )
    p.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )
    return p


def run() -> None:
    """Main entrypoint invoked by the ``deskflow`` console script."""
    parser = _build_parser()
    args = parser.parse_args()

    target: Path = args.file.resolve()
    _ensure_file(target)

    # Load config (creates ~/.config/deskflow/config.toml if missing)
    config = load_config()

    # Import here to avoid slow Textual import on --version / --help
    from deskflow.app import DeskFlowApp

    app = DeskFlowApp(target_file=target, config=config)
    app.run()


if __name__ == "__main__":
    run()
