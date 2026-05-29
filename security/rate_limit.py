"""In-memory rate limiter with cooldown and sliding-window caps."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from security import limits


@dataclass(frozen=True)
class RateLimitPolicy:
    cooldown_seconds: float
    window_seconds: int
    max_actions: int


class RateLimiter:
    def __init__(
        self,
        default_policy: RateLimitPolicy,
        per_action: dict[str, RateLimitPolicy] | None = None,
    ) -> None:
        self._default = default_policy
        self._per_action = per_action or {}
        self._last_action: dict[tuple[int, str], float] = {}
        self._window_actions: dict[tuple[int, str], deque[float]] = {}

    def _policy_for(self, action: str) -> RateLimitPolicy:
        return self._per_action.get(action, self._default)

    def limited(self, uid: int, action: str) -> bool:
        policy = self._policy_for(action)
        now = time.monotonic()

        last_key = (uid, action)
        last = self._last_action.get(last_key, 0.0)
        if policy.cooldown_seconds and now - last < policy.cooldown_seconds:
            return True

        window_key = (uid, action)
        window = self._window_actions.setdefault(window_key, deque())
        while window and now - window[0] > policy.window_seconds:
            window.popleft()
        if policy.max_actions and len(window) >= policy.max_actions:
            return True

        self._last_action[last_key] = now
        window.append(now)
        return False


def default_rate_limiter() -> RateLimiter:
    default_policy = RateLimitPolicy(
        cooldown_seconds=limits.RATE_LIMIT_COOLDOWN_SECONDS,
        window_seconds=limits.RATE_LIMIT_WINDOW_SECONDS,
        max_actions=limits.RATE_LIMIT_MAX_ACTIONS,
    )
    per_action = {
        "search": RateLimitPolicy(2.5, 60, 20),
        "info": RateLimitPolicy(2.0, 60, 30),
        "zip": RateLimitPolicy(5.0, 300, 5),
        "download": RateLimitPolicy(2.0, 120, 20),
        "upload": RateLimitPolicy(2.0, 120, 20),
        "delete": RateLimitPolicy(2.0, 120, 10),
        "rename": RateLimitPolicy(1.5, 60, 20),
        "move": RateLimitPolicy(1.5, 60, 20),
        "mkdir": RateLimitPolicy(1.5, 60, 20),
        "index": RateLimitPolicy(5.0, 300, 5),
    }
    return RateLimiter(default_policy, per_action)


_GLOBAL_LIMITER = default_rate_limiter()


def get_rate_limiter() -> RateLimiter:
    return _GLOBAL_LIMITER
