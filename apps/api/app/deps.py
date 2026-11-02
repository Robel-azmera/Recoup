"""Dependency container. Built once per app; tests swap in their own."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .config import Settings
from .integrations.store import FirestoreStore, MemoryStore, Store


@dataclass
class Container:
    settings: Settings
    store: Store


def build_container(settings: Settings) -> Container:
    store: Store
    if settings.store_backend == "memory":
        store = MemoryStore()
    else:
        if settings.firestore_emulator_host:
            import os

            os.environ.setdefault("FIRESTORE_EMULATOR_HOST", settings.firestore_emulator_host)
        store = FirestoreStore(settings.firestore_project_id)
    return Container(settings=settings, store=store)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
