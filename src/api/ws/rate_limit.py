"""Issue #43 - a plain in-process token bucket, no external dependency
(Redis, etc.) needed at this scale (a handful of invited players sharing one
local GPU/Ollama instance, see CLAUDE.md and the issue itself). Guards the
WebSocket messages that actually trigger a real LLM/GPU call
(player_action/player_move/debug_action, see api/ws/session.py) against a
misbehaving client submitting faster than the graph can realistically keep
up with - the passphrase gate (#42) already keeps out anonymous traffic, so
the remaining risk this closes is an *invited* client hammering the
endpoint, by accident or otherwise.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """capacity: max burst size. refill_per_second: steady-state rate. now_fn
    is injectable (defaults to time.monotonic) so tests don't need real
    sleeps - the same "don't assume real wall-clock time in a unit test"
    discipline this project already applies to RNG via injectable Random
    instances.
    """

    capacity: float
    refill_per_second: float
    now_fn: Callable[[], float] = time.monotonic
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = self.now_fn()

    def try_consume(self, cost: float = 1.0) -> bool:
        now = self.now_fn()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
        if self._tokens < cost:
            return False
        self._tokens -= cost
        return True
