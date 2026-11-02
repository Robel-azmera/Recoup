"""Dependency container. Built once per app (or per script); tests build their own."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Request

from .config import Settings
from .integrations.store import FirestoreStore, MemoryStore, Store
from .integrations.stripe_gateway import MockStripe, RealStripe, StripeGateway
from .services.audit import AuditLog
from .services.disputes import DisputeService
from .services.events import EventProcessor
from .services.merchant import MerchantData, StoreMerchantData
from .services.sync import SyncService


@dataclass
class Container:
    settings: Settings
    store: Store
    audit: AuditLog
    stripe: StripeGateway
    merchant: MerchantData
    sync: SyncService
    disputes: DisputeService
    events: EventProcessor


def build_container(settings: Settings, store: Store | None = None) -> Container:
    if store is None:
        if settings.store_backend == "memory":
            store = MemoryStore()
        else:
            if settings.firestore_emulator_host:
                os.environ.setdefault("FIRESTORE_EMULATOR_HOST", settings.firestore_emulator_host)
            store = FirestoreStore(settings.firestore_project_id)
    if settings.firebase_auth_emulator_host:
        os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", settings.firebase_auth_emulator_host)

    audit = AuditLog(store)
    stripe: StripeGateway = (
        RealStripe(settings.stripe_secret_key) if settings.stripe_secret_key else MockStripe(store)
    )
    merchant = StoreMerchantData(store)
    sync = SyncService(store, stripe, audit)
    disputes = DisputeService(store, stripe, merchant, audit)
    sync.disputes = disputes
    events = EventProcessor(store, disputes, sync, audit)

    if isinstance(stripe, MockStripe):

        async def emit(event: dict[str, object]) -> None:
            await events.process(event, source="mock")

        stripe.set_emitter(emit)

    return Container(
        settings=settings,
        store=store,
        audit=audit,
        stripe=stripe,
        merchant=merchant,
        sync=sync,
        disputes=disputes,
        events=events,
    )


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
