from datetime import timedelta

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.agent.llm import LLMError
from app.agent.mock_llm import ScriptedLLM
from app.deps import Container
from app.jobs.recon import run_reconciliation
from app.models import utcnow
from app.seed_data import build_dataset
from app.services.demo import seed


@pytest.fixture
async def seeded(container: Container) -> Container:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    return container


async def exceptions(c: Container) -> dict[str, dict]:  # type: ignore[type-arg]
    return {d["type"]: d for d in await c.store.query("recon_exceptions")}


async def test_run_finds_planted_anomalies_and_explains_them(seeded: Container) -> None:
    run = await run_reconciliation(seeded)
    assert run.status == "completed"
    assert run.exception_count == 6 and run.new_exceptions == 6
    assert set(run.by_type) == {
        "missing_settlement",
        "amount_mismatch",
        "unexpected_fee",
        "orphan_refund",
        "delayed_payout",
        "duplicate_charge",
    }
    assert run.matched == 24 and run.payouts_checked == 5
    assert run.open_discrepancy > 0
    for ex in (await exceptions(seeded)).values():
        assert ex["explanation"] and ex["suggested_action"]
        assert ex["explained_by"] == "mock-deterministic"
        assert ex["detail"] in ex["explanation"]
    steps = [e.step for e in await seeded.audit.recent(limit=100)]
    assert {"recon.started", "recon.exception_found", "recon.explained", "recon.completed"} <= set(
        steps
    )


async def test_rerun_is_idempotent_and_keeps_analyst_decisions(
    seeded: Container, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await run_reconciliation(seeded)
    ex = await exceptions(seeded)
    r = await client.post(
        f"/recon/exceptions/{ex['unexpected_fee']['id']}",
        json={"action": "expected", "note": "Amex surcharge"},
        headers=auth_headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "expected"
    await client.post(
        f"/recon/exceptions/{ex['delayed_payout']['id']}",
        json={"action": "snooze", "snooze_days": 2},
        headers=auth_headers,
    )

    second = await run_reconciliation(seeded, utcnow() + timedelta(seconds=5))
    assert second.new_exceptions == 0
    assert len(await seeded.store.query("recon_exceptions")) == 6
    after = await exceptions(seeded)
    assert after["unexpected_fee"]["status"] == "expected"
    assert after["unexpected_fee"]["resolution"] == "Amex surcharge"
    assert after["delayed_payout"]["status"] == "snoozed"

    # Snooze expires -> reopened on the next run.
    third = await run_reconciliation(seeded, utcnow() + timedelta(days=3))
    assert (await exceptions(seeded))["delayed_payout"]["status"] == "open"
    assert third.status == "completed"


async def test_fixed_data_auto_resolves(seeded: Container) -> None:
    await run_reconciliation(seeded)
    txn = await seeded.store.get("balance_txns", "txn_hist_009")
    order = await seeded.store.get("orders", "ord_h009")
    assert txn and order
    txn["amount"] = order["total"]  # someone corrected the ledger
    txn["net"] = txn["amount"] - txn["fee"]
    await seeded.store.set("balance_txns", "txn_hist_009", txn)
    p = await seeded.store.get("payouts", txn["payout_id"])
    assert p
    p["amount"] += 1000
    await seeded.store.set("payouts", p["id"], p)

    run = await run_reconciliation(seeded, utcnow() + timedelta(seconds=5))
    assert run.auto_resolved == 1
    mismatch = (await exceptions(seeded))["amount_mismatch"]
    assert mismatch["status"] == "resolved" and mismatch["resolved_by"] == "system"


async def test_llm_failure_or_invented_numbers_fall_back_to_templates(seeded: Container) -> None:
    seeded.llm = ScriptedLLM(turns=[], structured=[LLMError("quota"), LLMError("quota")])
    await run_reconciliation(seeded)
    assert {e["explained_by"] for e in (await exceptions(seeded)).values()} == {"template"}

    # A model answer that invents an amount is rejected for that item.
    for d in await seeded.store.query("recon_exceptions"):
        await seeded.store.delete("recon_exceptions", d["id"])
    first = await run_reconciliation(seeded, utcnow() + timedelta(seconds=1))
    ids = [d["id"] for d in await seeded.store.query("recon_exceptions")]
    for i in ids:
        doc = await seeded.store.get("recon_exceptions", i)
        assert doc
        doc["explanation"] = None
        await seeded.store.set("recon_exceptions", i, doc)
    seeded.llm = ScriptedLLM(
        turns=[],
        structured=[
            {
                "items": [
                    {
                        "exception_id": i,
                        "explanation": "Stripe withheld $9,999.00 for no reason.",
                        "suggested_action": "Wait.",
                    }
                    for i in ids
                ]
            }
        ],
    )
    await run_reconciliation(seeded, utcnow() + timedelta(seconds=2))
    assert first.status == "completed"
    assert {e["explained_by"] for e in (await exceptions(seeded)).values()} == {"template"}


async def test_api_summary_run_and_payout_view(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    empty = (await client.get("/recon/summary", headers=auth_headers)).json()
    assert empty["run"] is None and empty["exceptions"] == []
    run = (await client.post("/recon/run", headers=auth_headers)).json()
    assert run["status"] == "completed"
    s = (await client.get("/recon/summary?status=open", headers=auth_headers)).json()
    assert len(s["exceptions"]) == 6 and s["run"]["id"] == run["id"]

    views = (await client.get("/recon/payouts", headers=auth_headers)).json()
    assert len(views) == 5
    flagged = [line for v in views for line in v["lines"] if line["exception_ids"]]
    assert {line["txn"]["id"] for line in flagged} >= {
        "txn_hist_009",
        "txn_hist_013",
        "txn_re_hist_orphan",
        "txn_hist_dup",
    }
    delayed = [v for v in views if v["exception_ids"]]
    assert [v["payout"]["status"] for v in delayed] == ["in_transit"]
    matched = [
        line
        for v in views
        for line in v["lines"]
        if line["txn"]["type"] == "charge" and line["order_id"] and not line["exception_ids"]
    ]
    assert len(matched) == 24


async def test_failed_run_is_recorded(seeded: Container) -> None:
    await seeded.store.set("payouts", "po_bad", {"id": "po_bad"})  # malformed mirror
    with pytest.raises(ValidationError):
        await run_reconciliation(seeded)
    runs = await seeded.store.query("recon_runs")
    assert runs[0]["status"] == "failed" and runs[0]["error"]
