"""Drift-free Pomodoro timer engine.

Implements Risk C mitigation: all time computations are based on the difference
between a fixed target epoch timestamp and the current ``time.monotonic()`` value,
so event-loop jitter and UI redraws cannot cause cumulative clock drift.

    Remaining = max(0, target_epoch - time.monotonic())
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from deskflow.config import DeskFlowConfig


class SessionType(enum.Enum):
    """Possible Pomodoro session types."""

    WORK = "WORK"
    SHORT_BREAK = "SHORT BREAK"
    LONG_BREAK = "LONG BREAK"

    @property
    def label(self) -> str:
        return self.value

    @property
    def emoji(self) -> str:
        match self:
            case SessionType.WORK:
                return "🍅"
            case SessionType.SHORT_BREAK:
                return "☕"
            case SessionType.LONG_BREAK:
                return "🌴"


class TimerState(enum.Enum):
    """Runtime state of the Pomodoro timer."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


TickCallback = Callable[[float], Coroutine[Any, Any, None]]
CompleteCallback = Callable[[SessionType], Coroutine[Any, Any, None]]


@dataclass
class PomodoroTimer:
    """Epoch-anchored, drift-free Pomodoro countdown timer.

    The timer stores a *target monotonic timestamp* when started. On every tick
    the remaining time is computed as:

        remaining = max(0.0, target_epoch - time.monotonic())

    This means no drift accumulates across async event-loop ticks.

    Args:
        config: :class:`~deskflow.config.DeskFlowConfig` with session durations.
    """

    config: "DeskFlowConfig"

    # Internal state
    _session_type: SessionType = field(default=SessionType.WORK, init=False)
    _state: TimerState = field(default=TimerState.IDLE, init=False)
    _target_epoch: float = field(default=0.0, init=False)
    _pause_remaining: float = field(default=0.0, init=False)
    _pomodoro_count: int = field(default=0, init=False)
    _tick_callbacks: list[TickCallback] = field(default_factory=list, init=False)
    _complete_callbacks: list[CompleteCallback] = field(
        default_factory=list, init=False
    )
    _task: asyncio.Task | None = field(default=None, init=False)

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def session_type(self) -> SessionType:
        return self._session_type

    @property
    def state(self) -> TimerState:
        return self._state

    @property
    def pomodoro_count(self) -> int:
        """Number of completed work sessions in the current cycle (0–N)."""
        return self._pomodoro_count

    @property
    def is_running(self) -> bool:
        return self._state == TimerState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self._state == TimerState.PAUSED

    # ── Timing ────────────────────────────────────────────────────────────────

    def _duration_for(self, session: SessionType) -> float:
        """Return the duration in seconds for *session*."""
        match session:
            case SessionType.WORK:
                return self.config.work_seconds
            case SessionType.SHORT_BREAK:
                return self.config.short_break_seconds
            case SessionType.LONG_BREAK:
                return self.config.long_break_seconds

    def remaining(self) -> float:
        """Return seconds remaining in the current session (drift-free)."""
        if self._state == TimerState.RUNNING:
            return max(0.0, self._target_epoch - time.monotonic())
        if self._state == TimerState.PAUSED:
            return max(0.0, self._pause_remaining)
        if self._state == TimerState.FINISHED:
            return 0.0
        # IDLE: return full duration
        return self._duration_for(self._session_type)

    def remaining_fmt(self) -> str:
        """Return remaining time as ``MM:SS`` string."""
        secs = int(self.remaining())
        return f"{secs // 60:02d}:{secs % 60:02d}"

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_tick(self, callback: TickCallback) -> None:
        """Register an async callback invoked every 0.5 s with remaining seconds."""
        self._tick_callbacks.append(callback)

    def on_complete(self, callback: CompleteCallback) -> None:
        """Register an async callback invoked when a session finishes."""
        self._complete_callbacks.append(callback)

    async def _fire_tick(self, remaining: float) -> None:
        for cb in self._tick_callbacks:
            await cb(remaining)

    async def _fire_complete(self, session: SessionType) -> None:
        for cb in self._complete_callbacks:
            await cb(session)

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start (or resume) the timer.

        If the timer is paused, restores from the paused position.
        If idle/finished, starts a fresh session.
        """
        if self._state == TimerState.RUNNING:
            return

        if self._state == TimerState.PAUSED:
            # Resume from paused position
            self._target_epoch = time.monotonic() + self._pause_remaining
            self._pause_remaining = 0.0
        else:
            # Fresh start
            duration = self._duration_for(self._session_type)
            self._target_epoch = time.monotonic() + duration

        self._state = TimerState.RUNNING
        self._task = asyncio.get_event_loop().create_task(self._run_loop())

    def pause(self) -> None:
        """Pause the running timer, preserving the remaining time."""
        if self._state != TimerState.RUNNING:
            return
        self._pause_remaining = self.remaining()
        self._state = TimerState.PAUSED
        if self._task:
            self._task.cancel()
            self._task = None

    def reset(self) -> None:
        """Stop and reset the timer to the beginning of the current session."""
        if self._task:
            self._task.cancel()
            self._task = None
        self._state = TimerState.IDLE
        self._pause_remaining = 0.0
        self._target_epoch = 0.0

    def skip_session(self) -> None:
        """Immediately advance to the next session without completing the current one."""
        self.reset()
        self._advance_session(completed=False)

    # ── Session advancement ───────────────────────────────────────────────────

    def _advance_session(self, completed: bool = True) -> None:
        """Move to the next session in the Pomodoro cycle.

        Cycle: WORK → SHORT_BREAK → ... (×N) → LONG_BREAK → repeat
        """
        cfg = self.config.timer
        max_pomodoros = cfg.pomodoros_before_long_break

        if self._session_type == SessionType.WORK and completed:
            self._pomodoro_count += 1

        if self._session_type == SessionType.WORK:
            if self._pomodoro_count >= max_pomodoros:
                self._session_type = SessionType.LONG_BREAK
            else:
                self._session_type = SessionType.SHORT_BREAK
        else:
            # After any break, go back to work
            if self._session_type == SessionType.LONG_BREAK:
                self._pomodoro_count = 0
            self._session_type = SessionType.WORK

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Async loop that fires tick callbacks and detects session completion."""
        try:
            while True:
                await asyncio.sleep(0.5)
                rem = self.remaining()
                await self._fire_tick(rem)

                if rem <= 0.0 and self._state == TimerState.RUNNING:
                    completed_session = self._session_type
                    self._state = TimerState.FINISHED
                    await self._fire_complete(completed_session)
                    # Auto-advance session
                    self._advance_session(completed=True)
                    self._state = TimerState.IDLE
                    # Auto-start next session
                    self.start()
                    break
        except asyncio.CancelledError:
            pass

    # ── Dot progress indicator ────────────────────────────────────────────────

    def dot_progress(self) -> str:
        """Return a dot string showing Pomodoro progress, e.g. ``● ● ○ ○``."""
        max_p = self.config.timer.pomodoros_before_long_break
        done = min(self._pomodoro_count, max_p)
        filled = "●" * done
        empty = "○" * (max_p - done)
        dots = " ".join(list(filled) + list(empty))
        return f"{dots}  ({done}/{max_p})"
