"""Background task runner: keeps references to fire-and-forget tasks and logs failures.

Webhooks must return quickly, so agent drafting runs here. Tests and the seed script call
`drain()` to wait for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from ..logging import get_logger

log = get_logger("tasks")


class TaskRunner:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and (exc := t.exception()) is not None:
                log.error(
                    "background task failed", extra={"fields": {"task": name, "error": repr(exc)}}
                )

        task.add_done_callback(done)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @property
    def pending(self) -> int:
        return len(self._tasks)
