"""Dependency container. Built once per app (or per script); tests build their own."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Request

from .agent.dispute_agent import DisputeAgent
from .agent.llm import LLM, GeminiLLM, RateLimiter
from .agent.mock_llm import MockLLM
from .config import Settings
from .integrations.notify import (
    CalendarClient,
    EmailClient,
    MockCalendar,
    MockEmail,
    MockTelegram,
    RealCalendar,
    RealEmail,
    RealTelegram,
    TelegramClient,
)
from .integrations.store import FirestoreStore, MemoryStore, Store
from .integrations.stripe_gateway import MockStripe, RealStripe, StripeGateway
from .models import Dispute
from .policy.gate import PolicyGate
from .services.audit import AuditLog
from .services.disputes import DisputeService
from .services.efw import EfwService
from .services.events import EventProcessor
from .services.merchant import MerchantData, StoreMerchantData
from .services.notifications import NotificationService
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
    notifier: NotificationService = None  # type: ignore[assignment]
    efw: EfwService = None  # type: ignore[assignment]


def build_llm(settings: Settings) -> LLM:
    if settings.gemini_api_key:
        fallbacks = [m.strip() for m in settings.gemini_fallback_models.split(",") if m.strip()]
        return GeminiLLM(
            settings.gemini_api_key,
            settings.gemini_model,
            RateLimiter(settings.gemini_rpm),
            fallbacks,
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
    s = settings
    telegram: TelegramClient = (
        RealTelegram(s.telegram_bot_token)
        if s.telegram_bot_token and s.telegram_chat_id
        else MockTelegram(store)
    )
    calendar: CalendarClient = (
        RealCalendar(s.google_calendar_id, s.google_service_account_json)
        if s.google_calendar_id and s.google_service_account_json
        else MockCalendar(store)
    )
    email: EmailClient = (
        RealEmail(s.resend_api_key, s.email_from) if s.resend_api_key else MockEmail(store)
    )
    c.notifier = NotificationService(c, telegram, calendar, email)
    c.efw = EfwService(c)

    async def efw_new(e: object) -> None:  # W2: recommend + propose in the background
        from .models import EarlyFraudWarning

        assert isinstance(e, EarlyFraudWarning)
        c.tasks.spawn(c.efw.handle(e), name=f"efw:{e.id}")

    events.on_efw.append(efw_new)

    if auto_draft:  # W1: every new dispute gets a draft, in the background (webhooks stay fast)

        async def draft_and_alert(dispute_id: str) -> None:
            drafted = await c.agent.draft(dispute_id)
            await c.notifier.ensure_alerted(drafted)

        async def draft_new(d: Dispute) -> None:
            c.tasks.spawn(draft_and_alert(d.id), name=f"draft:{d.id}")

        disputes.on_created.append(draft_new)
    return c


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container
