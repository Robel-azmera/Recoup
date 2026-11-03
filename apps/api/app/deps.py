"""Dependency container. Built once per app (or per script); tests build their own."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Request

from .agent.dispute_agent import DisputeAgent
from .agent.llm import LLM, GeminiLLM, RateLimiter
from .agent.mock_llm import MockLLM
from .config import Settings
from .integrations.store import FirestoreStore, MemoryStore, Store
from .integrations.stripe_gateway import MockStripe, RealStripe, StripeGateway
from .models import Dispute
from .policy.gate import PolicyGate
from .services.audit import AuditLog
from .services.disputes import DisputeService
from .services.events import EventProcessor
from .services.merchant import MerchantData, StoreMerchantData
from .services.sync import SyncService
from .services.tasks import TaskRunner


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
    gate: PolicyGate
    tasks: TaskRunner
    llm: LLM
    agent: DisputeAgent = None  # type: ignore[assignment]  # set right after construction


def build_llm(settings: Settings) -> LLM:
    if settings.gemini_api_key:
        return GeminiLLM(
            settings.gemini_api_key, settings.gemini_model, RateLimiter(settings.gemini_rpm)
        )
    return MockLLM()


def build_container(
    settings: Settings, store: Store | None = None, llm: LLM | None = None, auto_draft: bool = True
) -> Container:
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

    c = Container(
        settings=settings,
        store=store,
        audit=audit,
        stripe=stripe,
        merchant=merchant,
        sync=sync,
        disputes=disputes,
        events=events,
        gate=PolicyGate(store, stripe, audit, settings),
        tasks=TaskRunner(),
        llm=llm or build_llm(settings),
    )
    c.agent = DisputeAgent(c, c.llm)

    if auto_draft:  # W1: every new dispute gets a draft, in the background (webhooks stay fast)

        async def draft_new(d: Dispute) -> None:
            c.tasks.spawn(c.agent.draft(d.id), name=f"draft:{d.id}")

        disputes.on_created.append(draft_new)
    return c


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
