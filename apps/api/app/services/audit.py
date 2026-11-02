"""Append-only audit log. Every agent/system/user step lands here; the UI feed listens to it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ..integrations.store import Filter, Store
from ..logging import get_logger
from ..models import Actor, AuditEntry, to_doc

log = get_logger("audit")

_MAX_FIELD_CHARS = 4000


def _clip(value: Any) -> Any:
    """Keep audit docs well under Firestore's 1 MiB doc limit."""
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + "…"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:50]]
    return value


class AuditLog:
    COLLECTION = "audit_log"

    def __init__(self, store: Store) -> None:
        self._store = store

    async def log(
        self,
        step: str,
        *,
        actor: Actor = Actor.system,
        tool: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        subject_id: str | None = None,
        links: dict[str, str] | None = None,
        level: str = "info",
    ) -> AuditEntry:
        now = datetime.now(UTC)
        # Millisecond precision + random suffix: ids sort by time and never collide.
        entry = AuditEntry(
            id=f"{now.strftime('%Y%m%dT%H%M%S%f')[:-3]}-{uuid.uuid4().hex[:6]}",
            ts=now,
            actor=actor,
            step=step,
            tool=tool,
            input=_clip(input) if input else None,
            output=_clip(output) if output else None,
            subject_id=subject_id,
            links=links or {},
            level=level,
        )
        doc = to_doc(entry)
        await self._store.set(self.COLLECTION, entry.id, doc)
        log.info(step, extra={"fields": {"audit": True, "subject_id": subject_id, "tool": tool}})
        return entry

    async def recent(self, limit: int = 50, subject_id: str | None = None) -> list[AuditEntry]:
        filters: list[Filter] = [("subject_id", "==", subject_id)] if subject_id else []
        docs = await self._store.query(
            self.COLLECTION, filters, order_by="id", descending=True, limit=limit
        )
        return [AuditEntry.model_validate(d) for d in docs]
