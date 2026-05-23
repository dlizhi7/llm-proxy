from __future__ import annotations

from logging import Logger
from threading import Lock
import time


class ApiKeyPool:
    """Round-robin key selector with temporary cooldown on rate-limit errors."""

    def __init__(self, keys: list[str], cooldown_seconds: float, logger: Logger) -> None:
        self._keys = keys
        self._cooldown_seconds = cooldown_seconds
        self._logger = logger
        self._index = 0
        self._cooldowns: dict[int, float] = {}
        self._lock = Lock()

    @property
    def count(self) -> int:
        return len(self._keys)

    def label(self, index: int) -> str:
        return f"key#{index + 1}"

    def next_key(self) -> tuple[int, str]:
        if not self._keys:
            raise RuntimeError("DEEPSEEK_API_KEY or DEEPSEEK_API_KEYS is not set")

        with self._lock:
            now = time.monotonic()
            for _ in range(len(self._keys)):
                index = self._index % len(self._keys)
                self._index = (self._index + 1) % len(self._keys)
                if self._cooldowns.get(index, 0) <= now:
                    return index, self._keys[index]

            # All keys are cooling down. Pick the earliest one to avoid deadlock.
            index = min(
                range(len(self._keys)),
                key=lambda item: self._cooldowns.get(item, 0),
            )
            self._logger.warning("all upstream keys cooling down; trying %s", self.label(index))
            return index, self._keys[index]

    def mark_rate_limited(self, index: int, reason: str) -> None:
        with self._lock:
            self._cooldowns[index] = time.monotonic() + self._cooldown_seconds

        self._logger.warning(
            "upstream %s rate limited; cooldown %.0fs; reason=%s",
            self.label(index),
            self._cooldown_seconds,
            reason[:200],
        )

    def cooling_count(self) -> int:
        now = time.monotonic()
        with self._lock:
            return sum(1 for deadline in self._cooldowns.values() if deadline > now)


def is_rate_limit_error(status_code: int, payload_text: str) -> bool:
    if status_code == 429:
        return True

    text = payload_text.lower()
    return (
        "rate limit" in text
        or "rate_limit" in text
        or "too many requests" in text
        or "限流" in text
    )
