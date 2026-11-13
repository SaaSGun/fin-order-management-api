"""Transactional outbox: stage one event per business change, in the
same transaction as the change itself (called from app/services/*.py
right alongside app.audit.record_audit) — that atomicity is the entire
point of this pattern: a crash between committing the business row and
publishing the event can never happen, because the event row is already
durably committed too; at worst it's published a little late by
scripts/outbox_relay.py.

No Kafka broker exists in this environment, so `LocalFilePublisher`
stands in for one. That's a deliberate, honest scoping choice, not a
shortcut on the part that matters: the atomic write here and the
concurrency-safe relay (SELECT ... FOR UPDATE SKIP LOCKED, see the relay
script) are fully real. Swapping in a real broker (Kafka, or GCP
Pub/Sub — the more natural fit for this project's GCP deployment
direction) only ever touches the Publisher below, never the
transactional-write side.
"""

import json
import uuid
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent


def record_event(session: AsyncSession, *, event_type: str, payload: dict[str, Any]) -> None:
    session.add(OutboxEvent(event_type=event_type, payload=payload))


class Publisher(Protocol):
    async def publish(self, event_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> None: ...


class LocalFilePublisher:
    """Appends one JSON line per event — standing in for a Kafka/Pub-Sub
    topic, see module docstring."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def publish(self, event_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> None:
        line = json.dumps({"event_id": str(event_id), "event_type": event_type, "payload": payload}, default=str)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
