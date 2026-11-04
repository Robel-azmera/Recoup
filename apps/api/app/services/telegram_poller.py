"""Local development: Telegram can't reach localhost, so poll getUpdates instead of a webhook.

The loop must never die: every step (including the initial deleteWebhook) is retried with
backoff, so a network blip at startup can't silently stop button handling for the session.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..integrations.notify import RealTelegram
from ..logging import get_logger

if TYPE_CHECKING:
    from ..deps import Container

log = get_logger("telegram")
MAX_BACKOFF_S = 60


def polling_enabled(c: Container) -> bool:
    tg = c.notifier.telegram
    mode = c.settings.telegram_mode
    return isinstance(tg, RealTelegram) and (
        mode == "polling" or (mode == "auto" and c.settings.env == "local")
    )


async def poll_forever(c: Container) -> None:
    tg = c.notifier.telegram
    assert isinstance(tg, RealTelegram)
    offset: int | None = None
    webhook_cleared = False
    backoff = 2
    while True:
        try:
            if not webhook_cleared:
                await tg.call("deleteWebhook")  # getUpdates fails while a webhook is set
                webhook_cleared = True
                log.info("telegram long-polling started")
            for update in await tg.get_updates(offset):
                offset = int(update["update_id"]) + 1
                try:
                    await c.notifier.handle_update(update)
                except Exception as e:  # one bad update must not stop the loop
                    log.error("telegram update failed", extra={"fields": {"error": repr(e)}})
            backoff = 2
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(
                "telegram poll error, retrying",
                extra={"fields": {"error": repr(e), "retry_in_s": backoff}},
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)


def on_poller_done(task: asyncio.Task[None]) -> None:
    """Make an unexpected poller exit loud instead of silent."""
    if not task.cancelled() and (exc := task.exception()) is not None:
        log.error("telegram poller stopped", extra={"fields": {"error": repr(exc)}})
