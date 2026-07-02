"""Configuration loader for DeskFlow.

Reads ``~/.config/deskflow/config.toml`` and creates the file with sensible
defaults if it does not yet exist.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


_CONFIG_DIR = Path.home() / ".config" / "deskflow"
_CONFIG_PATH = _CONFIG_DIR / "config.toml"

_DEFAULT_TOML = """\
[timer]
work_minutes = 25
short_break_minutes = 5
long_break_minutes = 15
pomodoros_before_long_break = 4

[notifications]
bell = true
desktop = true
"""


@dataclass
class TimerConfig:
    work_minutes: float = 25.0
    short_break_minutes: float = 5.0
    long_break_minutes: float = 15.0
    pomodoros_before_long_break: int = 4


@dataclass
class NotificationConfig:
    bell: bool = True
    desktop: bool = True


@dataclass
class DeskFlowConfig:
    timer: TimerConfig
    notifications: NotificationConfig

    @property
    def work_seconds(self) -> float:
        return self.timer.work_minutes * 60

    @property
    def short_break_seconds(self) -> float:
        return self.timer.short_break_minutes * 60

    @property
    def long_break_seconds(self) -> float:
        return self.timer.long_break_minutes * 60


def _ensure_config_exists() -> None:
    """Create the default config file if it does not exist."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not _CONFIG_PATH.exists():
        _CONFIG_PATH.write_text(_DEFAULT_TOML, encoding="utf-8")


def load_config() -> DeskFlowConfig:
    """Load :class:`DeskFlowConfig` from ``~/.config/deskflow/config.toml``.

    Creates the file with defaults if it does not exist.

    Returns:
        A fully-populated :class:`DeskFlowConfig` instance.
    """
    _ensure_config_exists()

    raw = _CONFIG_PATH.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))

    timer_data = data.get("timer", {})
    notif_data = data.get("notifications", {})

    timer = TimerConfig(
        work_minutes=float(timer_data.get("work_minutes", 25)),
        short_break_minutes=float(timer_data.get("short_break_minutes", 5)),
        long_break_minutes=float(timer_data.get("long_break_minutes", 15)),
        pomodoros_before_long_break=int(
            timer_data.get("pomodoros_before_long_break", 4)
        ),
    )
    notifications = NotificationConfig(
        bell=bool(notif_data.get("bell", True)),
        desktop=bool(notif_data.get("desktop", True)),
    )

    return DeskFlowConfig(timer=timer, notifications=notifications)
