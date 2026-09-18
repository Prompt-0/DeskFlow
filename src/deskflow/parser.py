"""Markdown state-machine parser and file synchronization engine.

Implements Risk B mitigation: robust line-by-line parsing that correctly handles
nested lists, code blocks, inline formatting, and exact line-indexed writes back
to the source file without disturbing unrelated content.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

# Regex: captures (indent)(marker)(checked_char)(text)
# Handles: - [ ] task, * [x] task, + [X] task, - [-] task
_TASK_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX_\-])\]\s+(.*)$")

# Code fence: ``` or ~~~
_CODE_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclass
class Task:
    """Represents a single parsed checklist item from a Markdown file."""

    id: str                  # UUID4 string for stable widget identity
    line_index: int          # 0-indexed line number in the source file
    indent: str              # Leading whitespace (preserved verbatim)
    checked: bool            # True if marker is x / X (not space or -)
    text: str                # Display text (inline formatting preserved)
    raw_line: str            # Unaltered source line (including newline)

    @classmethod
    def from_match(cls, match: re.Match, line_index: int, raw_line: str) -> "Task":
        indent, marker_char, text = match.groups()
        checked = marker_char.lower() == "x"
        return cls(
            id=str(uuid.uuid4()),
            line_index=line_index,
            indent=indent,
            checked=checked,
            text=text,
            raw_line=raw_line,
        )


@dataclass
class ParsedDocument:
    """Full parsed state of a Markdown file."""

    tasks: list[Task]
    lines: list[str]   # All raw lines (preserving newlines)
    sha256: str        # Hex SHA-256 of the full file bytes at parse time

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def checked_count(self) -> int:
        return sum(1 for t in self.tasks if t.checked)


class LineInfo(NamedTuple):
    """Metadata about a single display line (task or non-task)."""

    is_task: bool
    task: Task | None         # Set only if is_task is True
    display_text: str         # Text to show in the UI


def compute_sha256(content: bytes) -> str:
    """Return lowercase hex SHA-256 digest of *content*."""
    return hashlib.sha256(content).hexdigest()


def parse_markdown(path: Path) -> ParsedDocument:
    """Parse a Markdown file into a :class:`ParsedDocument`.

    Uses a line-by-line state machine that tracks multi-line code blocks so
    that checklist items inside fenced code zones are **not** treated as tasks.

    Args:
        path: Absolute path to the Markdown file to parse.

    Returns:
        A :class:`ParsedDocument` with all discovered tasks and raw lines.
    """
    content = path.read_bytes()
    sha = compute_sha256(content)
    raw_text = content.decode("utf-8", errors="replace")

    lines = raw_text.splitlines(keepends=True)
    tasks: list[Task] = []

    in_code_block = False

    for idx, line in enumerate(lines):
        # Track code fence entry/exit
        if _CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            continue

        # Skip content inside code blocks
        if in_code_block:
            continue

        m = _TASK_RE.match(line)
        if m:
            tasks.append(Task.from_match(m, idx, line))

    return ParsedDocument(tasks=tasks, lines=lines, sha256=sha)


def build_display_lines(doc: ParsedDocument) -> list[LineInfo]:
    """Extract displayable lines (tasks and non-tasks) from a Markdown file.

    Non-task lines (headers, paragraphs, etc.) are included as read-only entries
    with ``is_task=False``.

    Args:
        doc: The parsed Markdown document.

    Returns:
        List of :class:`LineInfo` items in document order.
    """
    lines = doc.lines

    result: list[LineInfo] = []
    in_code_block = False
    task_map: dict[int, Task] = {}

    for t in doc.tasks:
        task_map[t.line_index] = t

    for idx, line in enumerate(lines):
        stripped = line.rstrip("\n\r")

        if _CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            result.append(LineInfo(is_task=False, task=None, display_text=stripped))
            continue

        if idx in task_map:
            t = task_map[idx]
            result.append(LineInfo(is_task=True, task=t, display_text=t.text))
        else:
            # Only include non-empty lines as display items
            if stripped:
                result.append(LineInfo(is_task=False, task=None, display_text=stripped))

    return result


def write_task_state(file_path: Path, task: Task, checked: bool) -> bytes:
    """Toggle a task's checked state in-place and return the new SHA-256 hash.

    Only the single line at *task.line_index* is rewritten. All other lines
    are preserved verbatim, including their original line endings.

    Args:
        file_path: Path to the Markdown file.
        task: The :class:`Task` to update.
        checked: The desired new state (True = checked, False = unchecked).

    Returns:
        The SHA-256 hex digest of the updated file content as bytes.

    Raises:
        ValueError: If *task.line_index* is out of range for the file.
    """
    content = file_path.read_bytes()
    lines = content.decode("utf-8", errors="replace").splitlines(keepends=True)

    if task.line_index >= len(lines):
        raise ValueError(
            f"line_index {task.line_index} is out of range "
            f"(file has {len(lines)} lines)"
        )

    marker = "x" if checked else " "
    # Preserve the original line ending of the line being replaced
    original = lines[task.line_index]
    ending = ""
    if original.endswith("\r\n"):
        ending = "\r\n"
    elif original.endswith("\n"):
        ending = "\n"
    elif original.endswith("\r"):
        ending = "\r"

    lines[task.line_index] = f"{task.indent}- [{marker}] {task.text}{ending}"

    new_content = "".join(lines).encode("utf-8")
    file_path.write_bytes(new_content)
    return compute_sha256(new_content).encode()


def reload_if_changed(file_path: Path, known_sha: str) -> ParsedDocument | None:
    """Check if *file_path* has changed since *known_sha* and parse if so.

    Args:
        file_path: Path to the Markdown file.
        known_sha: Last known SHA-256 hex digest.

    Returns:
        A new :class:`ParsedDocument` if the file changed, or ``None`` if unchanged.
    """
    try:
        content = file_path.read_bytes()
    except OSError:
        return None

    current_sha = compute_sha256(content)
    if current_sha == known_sha:
        return None

    return parse_markdown(file_path)
