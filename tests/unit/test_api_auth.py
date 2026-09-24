"""Unit guards for api/auth: sessions, limiter, LAN predicate (slice 1A-7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from muhideen.api.auth import RateLimiter, SessionStore, is_lan

pytestmark = pytest.mark.unit

KL = timezone(timedelta(hours=8))
PINNED = datetime(2025, 10, 20, 12, 20, tzinfo=KL)


class FakeClock:
    def __init__(self) -> None:
        self._now = PINNED
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono = self._mono + seconds


def test_issue_and_validate() -> None:
    clock = FakeClock()
    store = SessionStore(clock)
    token = store.issue()
    assert isinstance(token, str) and token
    assert store.validate(token) is True


def test_expiry_purge_after_ttl() -> None:
    clock = FakeClock()
    store = SessionStore(clock)
    token = store.issue()
    clock.advance(1801.0)
    assert store.validate(token) is False


def test_revoke() -> None:
    clock = FakeClock()
    store = SessionStore(clock)
    token = store.issue()
    store.revoke(token)
    assert store.validate(token) is False


def test_five_then_429() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    key = "1.2.3.4"
    for _ in range(5):
        assert limiter.allow(key) is True
    assert limiter.allow(key) is False


def test_window_reset_at_61s() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    key = "1.2.3.4"
    for _ in range(5):
        assert limiter.allow(key) is True
    assert limiter.allow(key) is False
    clock.advance(61.0)
    assert limiter.allow(key) is True


def test_per_key_independence() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    for _ in range(5):
        assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("1.1.1.1") is False
    assert limiter.allow("2.2.2.2") is True


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("192.168.1.10", True),
        ("10.0.0.5", True),
        ("172.16.0.1", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("testclient", False),
    ],
)
def test_is_lan(host: str, expected: bool) -> None:
    assert is_lan(host) is expected
