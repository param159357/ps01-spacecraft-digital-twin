"""In-process publish/subscribe for the live stream.

Per-client queues are bounded. A client that cannot keep up is marked overflowed and told
to reconnect for a fresh snapshot rather than being allowed to accumulate unbounded
backlog (CLAUDE.md section 12.3).

`event_seq` is monotonic within this server session only; durable stream replay across a
restart is deliberately out of scope, so every reconnect fetches a fresh snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.contracts.common import Role
from app.contracts.telemetry import StreamEvent, StreamEventType

logger = logging.getLogger("ps01.bus")

MAX_QUEUE = 200


# eq=False keeps identity hashing, so each connection is its own set member even when
# two clients share a role.
@dataclass(eq=False)
class Subscriber:
    role: Role
    queue: asyncio.Queue[StreamEvent] = field(default_factory=lambda: asyncio.Queue(MAX_QUEUE))
    overflowed: bool = False

    def offer(self, event: StreamEvent) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop the client rather than the bound. It reconnects to a fresh snapshot.
            self.overflowed = True
            logger.warning("subscriber (%s) fell behind; will be asked to resubscribe", self.role)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._event_seq = 0

    @property
    def event_seq(self) -> int:
        return self._event_seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, role: Role) -> Subscriber:
        subscriber = Subscriber(role=role)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def next_seq(self) -> int:
        self._event_seq += 1
        return self._event_seq

    def publish(
        self, event_type: StreamEventType, payload: dict[str, Any], run_id: UUID | None = None
    ) -> StreamEvent:
        event = StreamEvent(
            type=event_type,
            event_seq=self.next_seq(),
            run_id=run_id,
            emitted_at_utc=datetime.now(UTC),
            payload=payload,
        )
        for subscriber in list(self._subscribers):
            subscriber.offer(event)
        return event
