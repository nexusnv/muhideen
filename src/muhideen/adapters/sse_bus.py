"""In-process SSE fan-out: queue-per-subscriber event bus."""

from __future__ import annotations

import queue
import threading
from collections.abc import Sequence


class SSEBus:
    """``EventBus`` over one ``Queue`` per subscriber.

    ``publish`` carries the ``config-update`` payload (``changed`` groups)
    through the port; ``state``/``tick`` publish with the default empty
    tuple. Publishing with zero subscribers is a no-op.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[tuple[str, tuple[str, ...]]]] = []

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def subscribe(self) -> queue.Queue[tuple[str, tuple[str, ...]]]:
        subscriber: queue.Queue[tuple[str, tuple[str, ...]]] = queue.Queue()
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[tuple[str, tuple[str, ...]]]) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, event: str, changed: Sequence[str] = ()) -> None:
        payload = (event, tuple(changed))
        with self._lock:
            targets = list(self._subscribers)
        for target in targets:
            target.put(payload)
