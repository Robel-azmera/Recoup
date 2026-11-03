"""Point the Telegram bot at the deployed API (prod). Locally the API long-polls instead.

    uv run --directory apps/api python ../../scripts/set_telegram_webhook.py https://<api-host>
"""

import asyncio
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIR))
os.chdir(API_DIR)


async def main() -> int:
    from app.config import get_settings
    from app.integrations.notify import RealTelegram

    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    s = get_settings()
    if not s.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set")
        return 2
    url = sys.argv[1].rstrip("/") + "/telegram/webhook"
    result = await RealTelegram(s.telegram_bot_token).set_webhook(url, s.telegram_webhook_secret)
    print(f"setWebhook {url}: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
