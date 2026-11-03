import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic.json_schema import models_json_schema

from .config import Settings, get_settings
from .deps import Container, build_container
from .logging import configure_logging
from .models import SCHEMA_MODELS
from .routers import actions, admin, auth, demo, disputes, health, recon, telegram, webhooks
from .services.telegram_poller import poll_forever, polling_enabled


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    c = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        poller = asyncio.create_task(poll_forever(c)) if polling_enabled(c) else None
        yield
        if poller:
            poller.cancel()
            with suppress(asyncio.CancelledError):
                await poller
        await c.tasks.drain()

    app = FastAPI(title="Recoup API", version="0.1.0", lifespan=lifespan)
    app.state.container = c

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (health, auth, webhooks, telegram, disputes, actions, recon, demo, admin):
        app.include_router(r.router)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        _, defs = models_json_schema(
            [(m, "serialization") for m in SCHEMA_MODELS],
            ref_template="#/components/schemas/{model}",
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for name, s in defs.get("$defs", {}).items():
            components.setdefault(name, s)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


app = create_app()
