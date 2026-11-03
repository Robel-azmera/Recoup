from ..integrations.store import Store
from ..models import AppSettings, to_doc


async def load_settings(store: Store) -> AppSettings:
    doc = await store.get("settings", "app")
    return AppSettings.model_validate(doc) if doc else AppSettings()


async def save_settings(store: Store, s: AppSettings) -> None:
    await store.set("settings", "app", to_doc(s))
