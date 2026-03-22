"""
License: MIT
Description: Adaptive polling delay — ramps up when idle, snaps back when changes detected.
"""

from __future__ import annotations


class AdaptivePollDelay:
    """
    Adaptive delay between polls that responds to state changes.

    - Starts at `min_delay`
    - No change: multiply by `backoff` (capped at `max_delay`)
    - Change detected: reset to `min_delay` for `burst_polls` consecutive polls,
      then resume backoff
    - After `cooldown_after` consecutive no-change polls, jump straight to `max_delay`

    Usage:
        delay = AdaptivePollDelay()
        while not done:
            job = poll()
            changed = detect_change(job, prev)
            wait = delay.next(changed)
            sleep(wait)
    """

    __slots__ = (
        "min_delay", "max_delay", "backoff", "burst_polls",
        "cooldown_after", "_current", "_no_change_count", "_burst_remaining",
    )

    def __init__(
        self,
        min_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff: float = 1.5,
        burst_polls: int = 2,
        cooldown_after: int = 20,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.backoff = backoff
        self.burst_polls = burst_polls
        self.cooldown_after = cooldown_after
        self._current = min_delay
        self._no_change_count = 0
        self._burst_remaining = 0

    def next(self, changed: bool) -> float:
        """Return the delay to sleep before the next poll."""
        if changed:
            self._no_change_count = 0
            self._burst_remaining = self.burst_polls
            self._current = self.min_delay
            return self._current

        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            self._current = self.min_delay
            return self._current

        self._no_change_count += 1

        if self._no_change_count >= self.cooldown_after:
            self._current = self.max_delay
            return self._current

        self._current = min(self._current * self.backoff, self.max_delay)
        return self._current

    def reset(self) -> None:
        """Reset to initial state."""
        self._current = self.min_delay
        self._no_change_count = 0
        self._burst_remaining = 0

    @property
    def current(self) -> float:
        return self._current
