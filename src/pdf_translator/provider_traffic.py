from __future__ import annotations

import threading
import time


class ProviderTrafficController:
    """Thread-safe request pacing and overload recovery for one provider."""

    def __init__(self, *, max_concurrency: int, rpm: int, tpm: int) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.rpm = max(1, int(rpm))
        self.tpm = max(1, int(tpm))
        self.current_concurrency = self.max_concurrency
        self._cooldown_until = 0.0
        self._last_request_at = 0.0
        self._successes_since_overload = 0
        self._active_requests = 0
        self._lock = threading.Lock()

    @property
    def minimum_request_interval(self) -> float:
        return 60.0 / self.rpm

    def request_interval(self, *, estimated_tokens: int = 0) -> float:
        token_interval = 60.0 * max(0, estimated_tokens) / self.tpm
        return max(self.minimum_request_interval, token_interval)

    def wait_for_request(self, *, estimated_tokens: int = 0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                ready_at = max(
                    self._cooldown_until,
                    self._last_request_at
                    + self.request_interval(estimated_tokens=estimated_tokens),
                )
                wait = ready_at - now
                if wait <= 0 and self._active_requests < self.current_concurrency:
                    self._last_request_at = now
                    self._active_requests += 1
                    return
            time.sleep(max(0.01, min(wait, 1.0)))

    def record_overload(self, *, retry_after: float | None = None) -> None:
        with self._lock:
            delay = max(float(retry_after or 0), 0.0)
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay)
            self.current_concurrency = max(1, self.current_concurrency // 2)
            self._successes_since_overload = 0
            self._active_requests = max(0, self._active_requests - 1)

    def record_success(self) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._successes_since_overload += 1
            if (
                self._successes_since_overload >= 8
                and self.current_concurrency < self.max_concurrency
            ):
                self.current_concurrency += 1
                self._successes_since_overload = 0

    def record_failure(self) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
