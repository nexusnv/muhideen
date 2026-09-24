"""Admin auth: sessions, rate limits, LAN predicate, admin dependency."""

from __future__ import annotations

import ipaddress
import secrets
import threading
from collections.abc import Callable

from fastapi import HTTPException, Request

from muhideen.core.ports import Clock

ADMIN_USERNAME = "admin"
SESSION_COOKIE = "muhideen-session"
SESSION_TTL_S = 1800.0
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW_S = 60.0
AUTH_401_DETAIL = "admin session required"


def is_lan(host: str) -> bool:
    """True for private or loopback IPs; fail-closed on unparseable input."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


class SessionStore:
    """In-memory expiring sessions keyed by secure random tokens."""

    def __init__(self, clock: Clock) -> None:
        """Hold the injected clock for TTL expiry checks."""
        self._clock = clock
        self._expiry: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        """Mint a random token expiring SESSION_TTL_S monotonic seconds out."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked()
            self._expiry[token] = self._clock.monotonic() + SESSION_TTL_S
        return token

    def validate(self, token: str) -> bool:
        """Return True when the token is present and unexpired."""
        with self._lock:
            self._purge_locked()
            return token in self._expiry

    def revoke(self, token: str) -> None:
        """Drop one session; unknown tokens are a no-op."""
        with self._lock:
            self._expiry.pop(token, None)

    def _purge_locked(self) -> None:
        """Delete expired sessions; caller must hold the lock."""
        now = self._clock.monotonic()
        expired = [token for token, until in self._expiry.items() if until <= now]
        for token in expired:
            del self._expiry[token]


class RateLimiter:
    """Sliding-window limiter keyed by client IP (single-worker in-memory)."""

    def __init__(
        self,
        clock: Clock,
        max_hits: int = RATE_LIMIT_MAX,
        window_s: float = RATE_LIMIT_WINDOW_S,
    ) -> None:
        """Hold the clock plus the per-key hit budget and window."""
        self._clock = clock
        self._max_hits = max_hits
        self._window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Consume one hit for ``key``; False when the window is exhausted."""
        now = self._clock.monotonic()
        cutoff = now - self._window_s
        with self._lock:
            recent = [stamp for stamp in self._hits.get(key, []) if stamp > cutoff]
            if len(recent) >= self._max_hits:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True


def require_admin(sessions: SessionStore) -> Callable[[Request], None]:
    """Dependency factory: 401 unless a valid admin session cookie is present."""

    def _check(request: Request) -> None:
        """Raise 401 unless the request carries a valid admin session."""
        token = request.cookies.get(SESSION_COOKIE)
        if token is None or not sessions.validate(token):
            raise HTTPException(status_code=401, detail=AUTH_401_DETAIL)

    return _check
