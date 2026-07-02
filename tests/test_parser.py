"""Unit tests for deskflow.parser module.

Covers:
- Parsing checked and unchecked tasks
- Multiple list markers (-, *, +)
- All checked variants (x, X, -)
- Skipping headers
- Skipping code blocks
- Nested indentation
- Inline formatting preservation
- write_task_state correctness (only target line changes)
- SHA-256 computation
- reload_if_changed behavior
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deskflow.parser import (
    Task,
    ParsedDocument,
    compute_sha256,
    parse_markdown,
    write_task_state,
    reload_if_changed,
    build_display_lines,
    _TASK_RE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_md(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp .md file and return its Path."""
    p = tmp_path / "test.md"
    p.write_text(content, encoding="utf-8")
    return p


# ── SHA-256 ───────────────────────────────────────────────────────────────────


def test_compute_sha256_basic() -> None:
    data = b"hello world"
    expected = hashlib.sha256(data).hexdigest()
    assert compute_sha256(data) == expected


def test_compute_sha256_empty() -> None:
    assert compute_sha256(b"") == hashlib.sha256(b"").hexdigest()


# ── Regex sanity ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line, expected_checked, expected_text",
    [
        ("- [ ] Unchecked task\n", False, "Unchecked task"),
        ("- [x] Checked task\n", True, "Checked task"),
        ("- [X] Also checked\n", True, "Also checked"),
        ("* [ ] Star marker\n", False, "Star marker"),
        ("+ [x] Plus marker\n", True, "Plus marker"),
        ("  - [ ] Indented task\n", False, "Indented task"),
        ("    * [X] Deeply indented\n", True, "Deeply indented"),
        ("- [-] Deprecated style\n", False, "Deprecated style"),
    ],
)
def test_task_regex_matches(line: str, expected_checked: bool, expected_text: str) -> None:
    m = _TASK_RE.match(line)
    assert m is not None, f"Expected regex to match: {line!r}"
    _, marker_char, text = m.groups()
    checked = marker_char.lower() == "x"
    assert checked == expected_checked
    assert text == expected_text


@pytest.mark.parametrize(
    "line",
    [
        "# Header\n",
        "## Section\n",
        "Normal paragraph text\n",
        "- Not a task item\n",
        "  * Not a task\n",
        "```python\n",
        "  code line\n",
    ],
)
def test_task_regex_does_not_match(line: str) -> None:
    assert _TASK_RE.match(line) is None, f"Should NOT match: {line!r}"


# ── parse_markdown ────────────────────────────────────────────────────────────


def test_parse_basic_unchecked(tmp_path: Path) -> None:
    p = make_md(tmp_path, "- [ ] Hello world\n")
    doc = parse_markdown(p)
    assert len(doc.tasks) == 1
    t = doc.tasks[0]
    assert t.text == "Hello world"
    assert t.checked is False
    assert t.indent == ""
    assert t.line_index == 0


def test_parse_basic_checked(tmp_path: Path) -> None:
    p = make_md(tmp_path, "- [x] Done task\n")
    doc = parse_markdown(p)
    assert len(doc.tasks) == 1
    assert doc.tasks[0].checked is True


def test_parse_checked_uppercase_x(tmp_path: Path) -> None:
    p = make_md(tmp_path, "- [X] Also done\n")
    doc = parse_markdown(p)
    assert doc.tasks[0].checked is True


def test_parse_multiple_tasks(tmp_path: Path) -> None:
    content = "- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert len(doc.tasks) == 3
    assert doc.tasks[0].text == "Task 1"
    assert doc.tasks[1].text == "Task 2"
    assert doc.tasks[1].checked is True
    assert doc.tasks[2].text == "Task 3"
    assert doc.tasks[2].line_index == 2


def test_parse_skips_headers(tmp_path: Path) -> None:
    content = "# Section Title\n## Sub Section\n- [ ] Real task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert len(doc.tasks) == 1
    assert doc.tasks[0].text == "Real task"
    assert doc.tasks[0].line_index == 2


def test_parse_skips_code_blocks(tmp_path: Path) -> None:
    content = (
        "# Code example\n"
        "```python\n"
        "- [ ] This is code, not a task\n"
        "x = 1\n"
        "```\n"
        "- [ ] Real task after code\n"
    )
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert len(doc.tasks) == 1
    assert doc.tasks[0].text == "Real task after code"


def test_parse_skips_tilde_code_blocks(tmp_path: Path) -> None:
    content = (
        "~~~\n"
        "- [ ] Inside tilde fence\n"
        "~~~\n"
        "- [x] Outside fence\n"
    )
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert len(doc.tasks) == 1
    assert doc.tasks[0].text == "Outside fence"


def test_parse_nested_indentation(tmp_path: Path) -> None:
    content = (
        "- [ ] Top level\n"
        "  - [ ] Indented by 2\n"
        "    - [x] Indented by 4\n"
    )
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert len(doc.tasks) == 3
    assert doc.tasks[0].indent == ""
    assert doc.tasks[1].indent == "  "
    assert doc.tasks[2].indent == "    "
    assert doc.tasks[2].checked is True


def test_parse_inline_formatting_preserved(tmp_path: Path) -> None:
    """Inline Markdown formatting inside task text must be preserved verbatim."""
    content = "- [ ] Fix **bold** issue in `code()` [link](http://example.com)\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert doc.tasks[0].text == "Fix **bold** issue in `code()` [link](http://example.com)"


def test_parse_task_with_brackets_in_text(tmp_path: Path) -> None:
    """Tasks with extra brackets in text should still parse correctly."""
    content = "- [ ] Implement feature [RFC-123]\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert doc.tasks[0].text == "Implement feature [RFC-123]"


def test_parse_sha256_matches_file(tmp_path: Path) -> None:
    content = "- [ ] Task A\n- [x] Task B\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    expected = compute_sha256(content.encode("utf-8"))
    assert doc.sha256 == expected


def test_parse_checked_count(tmp_path: Path) -> None:
    content = "- [ ] A\n- [x] B\n- [X] C\n- [ ] D\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert doc.task_count == 4
    assert doc.checked_count == 2


def test_parse_star_and_plus_markers(tmp_path: Path) -> None:
    content = "* [ ] Star task\n+ [x] Plus task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    assert len(doc.tasks) == 2
    assert doc.tasks[0].text == "Star task"
    assert doc.tasks[1].checked is True


# ── write_task_state ──────────────────────────────────────────────────────────


def test_write_task_state_check(tmp_path: Path) -> None:
    """Unchecked task should become checked after write."""
    content = "# Today\n- [ ] Task 1\n- [x] Task 2\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    task = doc.tasks[0]  # "Task 1" (unchecked)

    write_task_state(p, task, checked=True)

    new_content = p.read_text(encoding="utf-8")
    assert "- [x] Task 1" in new_content
    assert "- [x] Task 2" in new_content


def test_write_task_state_uncheck(tmp_path: Path) -> None:
    """Checked task should become unchecked after write."""
    content = "- [x] Done\n- [ ] Pending\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    task = doc.tasks[0]  # "Done" (checked)

    write_task_state(p, task, checked=False)

    new_content = p.read_text(encoding="utf-8")
    assert "- [ ] Done" in new_content
    assert "- [ ] Pending" in new_content


def test_write_task_state_preserves_surrounding_lines(tmp_path: Path) -> None:
    """Only the target line should change; all other lines must be verbatim."""
    content = (
        "# Header\n"
        "Some paragraph text.\n"
        "- [ ] The task\n"
        "Another paragraph.\n"
        "- [x] Other task\n"
    )
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    task = doc.tasks[0]  # "The task" at line 2

    write_task_state(p, task, checked=True)

    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    assert lines[0] == "# Header\n"
    assert lines[1] == "Some paragraph text.\n"
    assert lines[2] == "- [x] The task\n"
    assert lines[3] == "Another paragraph.\n"
    assert lines[4] == "- [x] Other task\n"


def test_write_task_state_returns_sha256(tmp_path: Path) -> None:
    """write_task_state should return a valid SHA-256 digest as bytes."""
    content = "- [ ] Task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)

    result_bytes = write_task_state(p, doc.tasks[0], checked=True)
    assert isinstance(result_bytes, bytes)
    # Verify the returned digest matches the actual file content
    actual_sha = compute_sha256(p.read_bytes())
    assert result_bytes.decode() == actual_sha


def test_write_task_state_nested_indentation(tmp_path: Path) -> None:
    """Indentation must be preserved after a write."""
    content = "  - [ ] Indented task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)

    write_task_state(p, doc.tasks[0], checked=True)

    new_content = p.read_text(encoding="utf-8")
    assert new_content == "  - [x] Indented task\n"


def test_write_task_state_out_of_range_raises(tmp_path: Path) -> None:
    """Should raise ValueError if task.line_index is beyond file length."""
    content = "- [ ] Task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)
    task = doc.tasks[0]
    # Manually corrupt the line_index
    task.line_index = 999

    with pytest.raises(ValueError, match="out of range"):
        write_task_state(p, task, checked=True)


# ── reload_if_changed ─────────────────────────────────────────────────────────


def test_reload_if_changed_no_change(tmp_path: Path) -> None:
    content = "- [ ] Task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)

    result = reload_if_changed(p, doc.sha256)
    assert result is None  # File hasn't changed


def test_reload_if_changed_detects_change(tmp_path: Path) -> None:
    content = "- [ ] Task\n"
    p = make_md(tmp_path, content)
    doc = parse_markdown(p)

    # Modify the file
    p.write_text("- [x] Task\n", encoding="utf-8")

    result = reload_if_changed(p, doc.sha256)
    assert result is not None
    assert result.tasks[0].checked is True


def test_reload_if_changed_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "nonexistent.md"
    result = reload_if_changed(p, "some_sha")
    assert result is None  # Should not raise


# ── build_display_lines ───────────────────────────────────────────────────────


def test_build_display_lines_includes_headers(tmp_path: Path) -> None:
    content = "# Today\n- [ ] Task A\n"
    p = make_md(tmp_path, content)
    lines = build_display_lines(p)
    # Should have both the header and the task
    assert len(lines) == 2
    assert lines[0].is_task is False
    assert "Today" in lines[0].display_text
    assert lines[1].is_task is True
    assert lines[1].task is not None
    assert lines[1].task.text == "Task A"


def test_build_display_lines_excludes_blank_lines(tmp_path: Path) -> None:
    content = "# Header\n\n- [ ] Task\n"
    p = make_md(tmp_path, content)
    lines = build_display_lines(p)
    # Blank line should be excluded from display
    texts = [li.display_text for li in lines]
    assert "" not in texts
