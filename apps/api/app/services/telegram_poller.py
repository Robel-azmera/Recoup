"""Local development: Telegram can't reach localhost, so poll getUpdates instead of a webhook."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..integrations.notify import RealTelegram
from ..logging import get_logger

if TYPE_CHECKING:
    from ..deps import Container

log = get_logger("telegram")


def polling_enabled(c: Container) -> bool:
    tg = c.notifier.telegram
    mode = c.settings.telegram_mode
    return isinstance(tg, RealTelegram) and (
        mode == "polling" or (mode == "auto" and c.settings.env == "local")
    )


async def poll_forever(c: Container) -> None:
    tg = c.notifier.telegram
    assert isinstance(tg, RealTelegram)
    await tg.call("deleteWebhook")  # getUpdates fails while a webhook is set
    offset: int | None = None
    log.info("telegram long-polling started")
    while True:
        try:
            for update in await tg.get_updates(offset):
                offset = int(update["update_id"]) + 1
                try:
                    await c.notifier.handle_update(update)
                except Exception as e:  # one bad update must not stop the loop
                    log.error("telegram update failed", extra={"fields": {"error": repr(e)}})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("telegram poll error", extra={"fields": {"error": repr(e)}})
            await asyncio.sleep(5)
