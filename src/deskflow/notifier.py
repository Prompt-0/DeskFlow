"""Cross-platform notification system for DeskFlow.

Supports:
- Terminal bell (\\a) — works universally
- Desktop notifications via ``notify-send`` (Linux) or ``osascript`` (macOS)

Both channels are independently togglable via :class:`~deskflow.config.NotificationConfig`.
Failures are silently swallowed so a missing ``notify-send`` never crashes the app.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deskflow.config import NotificationConfig


def _ring_bell() -> None:
    """Emit an ASCII BEL character to the terminal."""
    sys.stdout.write("\a")
    sys.stdout.flush()


def _send_desktop_notification(title: str, message: str) -> None:
    """Attempt to send a desktop notification.

    Uses ``notify-send`` on Linux/BSD and ``osascript`` on macOS.
    Any subprocess errors are silently ignored.
    """
    os_name = platform.system()
    try:
        if os_name == "Linux":
            subprocess.run(
                ["notify-send", "--app-name", "DeskFlow", title, message],
                check=False,
                timeout=3,
                capture_output=True,
            )
        elif os_name == "Darwin":
            script = (
                f'display notification "{message}" with title "{title}" '
                f'subtitle "DeskFlow"'
            )
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                timeout=3,
                capture_output=True,
            )
        # Windows / other platforms: no desktop notification, bell only
    except (OSError, subprocess.TimeoutExpired):
        pass


def notify(
    title: str,
    message: str,
    config: "NotificationConfig",
) -> None:
    """Send a notification using the channels enabled in *config*.

    Args:
        title: Notification title (e.g. "Pomodoro Complete!").
        message: Notification body text.
        config: :class:`~deskflow.config.NotificationConfig` controlling which
                channels are active.
    """
    if config.bell:
        _ring_bell()
    if config.desktop:
        _send_desktop_notification(title, message)
