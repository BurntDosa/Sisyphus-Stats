"""Small guards for duplicate Discord event delivery."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class DuplicateEventGuard:
    """Remember recently handled Discord event IDs for a bounded time."""

    def __init__(
        self,
        *,
        max_age_seconds: float = 600,
        max_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()

    def _prune(self, now: float) -> None:
        cutoff = now - self.max_age_seconds
        while self._seen:
            _, seen_at = next(iter(self._seen.items()))
            if seen_at > cutoff:
                break
            self._seen.popitem(last=False)

        while len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)

    def claim(self, event_key: str, *, now: float | None = None) -> bool:
        """Return true once per event key during the retention window."""
        now = self._clock() if now is None else now
        self._prune(now)
        if event_key in self._seen:
            return False
        self._seen[event_key] = now
        self._prune(now)
        return True

    def clear(self) -> None:
        """Clear remembered keys; used by offline checks and controlled tests."""
        self._seen.clear()
