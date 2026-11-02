import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import issue_token
from app.config import Settings
from app.deps import Container, build_container
from app.integrations.stripe_gateway import compute_signature_header
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "stripe_events"
WEBHOOK_SECRET = "whsec_test_secret"


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        store_backend="memory",
        stripe_webhook_secret=WEBHOOK_SECRET,
        admin_password="pw",
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_container(settings)


@pytest.fixture
async def client(settings: Settings, container: Container) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(settings, 'admin')}"}


def signed(event: dict[str, Any], secret: str = WEBHOOK_SECRET) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(event).encode()
    return payload, {
        "Stripe-Signature": compute_signature_header(payload, secret),
        "Content-Type": "application/json",
    }
