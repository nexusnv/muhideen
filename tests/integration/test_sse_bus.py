"""SSEBus adapter: queue-per-subscriber fan-out (slice 1A-7, Task 1)."""

from __future__ import annotations

import pytest

from muhideen.adapters.sse_bus import SSEBus
from muhideen.core.ports import EventBus

pytestmark = pytest.mark.integration


def test_sse_bus_satisfies_event_bus_port() -> None:
    assert isinstance(SSEBus(), EventBus)


def test_publish_with_zero_subscribers_is_noop() -> None:
    bus = SSEBus()
    bus.publish("state")
    assert bus.subscriber_count == 0


def test_subscribe_receives_published_event_with_changed() -> None:
    bus = SSEBus()
    queue = bus.subscribe()
    bus.publish("config-update", ("settings",))
    assert queue.get(timeout=1.0) == ("config-update", ("settings",))


def test_unsubscribe_stops_delivery() -> None:
    bus = SSEBus()
    queue = bus.subscribe()
    assert bus.subscriber_count == 1
    bus.unsubscribe(queue)
    assert bus.subscriber_count == 0
    bus.publish("state")
    assert queue.empty()


def test_each_subscriber_receives_every_publish() -> None:
    bus = SSEBus()
    first = bus.subscribe()
    second = bus.subscribe()
    assert bus.subscriber_count == 2
    bus.publish("tick")
    assert first.get(timeout=1.0) == ("tick", ())
    assert second.get(timeout=1.0) == ("tick", ())
