from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.deps import Container
from app.models import ActionStatus, ActionType, DisputeStatus, to_doc, utcnow
from app.policy.gate import PolicyError
from app.policy.tokens import action_hash, issue_approval_token, verify_approval_token
from app.seed_data import build_dataset
from app.services.demo import seed


@pytest.fixture
async def seeded(container: Container) -> Container:
    await seed(container, build_dataset(utcnow()))
    await container.tasks.drain()
    return container


async def fight_dispute(c: Container) -> Any:
    d = next(x for x in await c.disputes.list() if x.order_id == "ord_r000")
    a = await c.gate.get(d.proposed_action_id or "")
    assert a is not None and a.type == ActionType.submit_dispute_evidence
    return d, a


def test_hash_is_canonical_and_tokens_bind_to_hash() -> None:
    h1 = action_hash("t", "x", {"a": 1, "b": [1, 2]})
    assert h1 == action_hash("t", "x", {"b": [1, 2], "a": 1})
    assert h1 != action_hash("t", "x", {"a": 2, "b": [1, 2]})
    tok = issue_approval_token("s", "admin", "act_1", h1)
    assert verify_approval_token("s", tok, "admin", "act_1", h1) is None
    assert verify_approval_token("s", tok, "eve", "act_1", h1)
    assert verify_approval_token(
        "s", tok, "admin", "act_1", h1[:-1] + ("1" if h1[-1] == "0" else "0")
    )
    assert (
        verify_approval_token(
            "s", issue_approval_token("s", "admin", "act_1", h1, ttl=-1), "admin", "act_1", h1
        )
        == "token expired"
    )


async def test_approved_submit_reaches_stripe_and_closes_won(seeded: Container) -> None:
    d, a = await fight_dispute(seeded)
    assert a.payload["force_test_outcome"] == "won"
    done = await seeded.gate.approve(a.id, "admin", seeded.gate.mint_token(a, "admin"))
    assert done.status == ActionStatus.executed, done.error
    sd = await seeded.stripe.retrieve_dispute(d.id)
    assert sd["evidence"]["shipping_tracking_number"]
    assert "winning_evidence" in sd["evidence"]["uncategorized_text"]
    final = await seeded.disputes.get(d.id)
    assert final is not None and final.status == DisputeStatus.won
    assert final.outcome is not None and final.outcome.amount_recovered == d.amount


async def test_double_approval_executes_once(seeded: Container) -> None:
    d, a = await fight_dispute(seeded)
    tok = seeded.gate.mint_token(a, "admin")
    first = await seeded.gate.approve(a.id, "admin", tok)
    second = await seeded.gate.approve(a.id, "admin", tok)
    assert first.status == second.status == ActionStatus.executed
    sd = await seeded.stripe.retrieve_dispute(d.id)
    assert sd["evidence_details"]["submission_count"] == 1


async def test_token_for_other_user_rejected(seeded: Container) -> None:
    _, a = await fight_dispute(seeded)
    with pytest.raises(PolicyError) as e:
        await seeded.gate.approve(a.id, "admin", seeded.gate.mint_token(a, "mallory"))
    assert e.value.status_code == 403


async def test_tampered_payload_is_blocked(seeded: Container) -> None:
    _, a = await fight_dispute(seeded)
    tok = seeded.gate.mint_token(a, "admin")
    doc = await seeded.store.get("proposed_actions", a.id)
    assert doc is not None
    doc["payload"]["evidence"]["uncategorized_text"] = "Pay me."  # edited behind the gate's back
    await seeded.store.set("proposed_actions", a.id, doc)
    result = await seeded.gate.approve(a.id, "admin", tok)
    assert result.status == ActionStatus.blocked
    assert any(c.name == "payload_integrity" and not c.passed for c in result.policy_checks)


async def test_past_deadline_is_blocked(seeded: Container) -> None:
    d, a = await fight_dispute(seeded)
    d.due_by = utcnow() - timedelta(hours=1)
    await seeded.store.set("disputes", d.id, to_doc(d))
    result = await seeded.gate.approve(a.id, "admin", seeded.gate.mint_token(a, "admin"))
    assert result.status == ActionStatus.blocked
    assert "before_deadline" in (result.error or "")


async def test_cannot_submit_twice(seeded: Container) -> None:
    d, a = await fight_dispute(seeded)
    await seeded.gate.approve(a.id, "admin", seeded.gate.mint_token(a, "admin"))
    again = await seeded.gate.propose(
        ActionType.submit_dispute_evidence, d.id, {**a.payload, "note": "retry"}, amount=d.amount
    )
    result = await seeded.gate.approve(again.id, "admin", seeded.gate.mint_token(again, "admin"))
    assert result.status == ActionStatus.blocked
    assert "state_transition" in (result.error or "")


async def test_accept_loss(seeded: Container) -> None:
    weak = next(x for x in await seeded.disputes.list() if x.order_id == "ord_r004")
    a = await seeded.gate.get(weak.proposed_action_id or "")
    assert a is not None and a.type == ActionType.accept_dispute
    done = await seeded.gate.approve(a.id, "admin", seeded.gate.mint_token(a, "admin"))
    assert done.status == ActionStatus.executed
    final = await seeded.disputes.get(weak.id)
    assert final is not None and final.status == DisputeStatus.accepted
    assert final.outcome is not None and final.outcome.result == "accepted"


async def test_refund_rules_and_auto_policy(seeded: Container) -> None:
    efw = (await seeded.store.query("fraud_warnings"))[0]
    charge = await seeded.store.get("charges", efw["charge_id"])
    assert charge is not None
    too_much = await seeded.gate.propose(
        ActionType.refund_charge,
        efw["charge_id"],
        {"charge_id": efw["charge_id"], "amount": charge["amount"] + 1, "efw_id": efw["id"]},
        amount=charge["amount"] + 1,
    )
    r = await seeded.gate.approve(too_much.id, "admin", seeded.gate.mint_token(too_much, "admin"))
    assert r.status == ActionStatus.blocked and "within_remaining" in (r.error or "")

    ok = await seeded.gate.propose(
        ActionType.refund_charge,
        efw["charge_id"],
        {"charge_id": efw["charge_id"], "amount": charge["amount"], "efw_id": efw["id"]},
        amount=charge["amount"],
    )
    with pytest.raises(PolicyError):  # auto-refund policy is off by default: a human must approve
        await seeded.gate.auto_approve(ok.id)
    done = await seeded.gate.approve(ok.id, "admin", seeded.gate.mint_token(ok, "admin"))
    assert (
        done.status == ActionStatus.executed
        and done.result
        and done.result["amount"] == charge["amount"]
    )


async def test_editing_evidence_invalidates_old_approval(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    d, a = await fight_dispute(seeded)
    view = (await client.get(f"/actions/{a.id}", headers=auth_headers)).json()
    old_token = view["approval_token"]
    assert old_token and all(c["passed"] for c in view["action"]["policy_checks"])

    text = d.evidence_draft.evidence.uncategorized_text + " We are happy to provide more detail."
    r = await client.put(
        f"/disputes/{d.id}/evidence",
        json={"fields": {"uncategorized_text": text}},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    edited = r.json()
    user_sentences = [s for s in edited["evidence_draft"]["sentences"] if s["author"] == "user"]
    assert [s["text"] for s in user_sentences] == ["We are happy to provide more detail."]
    assert edited["evidence_draft"]["unsupported_sentence_ids"] == []
    assert edited["proposed_action_id"] != a.id

    stale = await client.post(
        f"/actions/{a.id}/approve", json={"approval_token": old_token}, headers=auth_headers
    )
    assert stale.status_code == 409  # superseded

    new_view = (
        await client.get(f"/actions/{edited['proposed_action_id']}", headers=auth_headers)
    ).json()
    ok = await client.post(
        f"/actions/{edited['proposed_action_id']}/approve",
        json={"approval_token": new_view["approval_token"]},
        headers=auth_headers,
    )
    assert ok.status_code == 200 and ok.json()["status"] == "executed"
    sd = await seeded.stripe.retrieve_dispute(d.id)
    assert "We are happy to provide more detail." in sd["evidence"]["uncategorized_text"]


async def test_override_to_fight_and_draft_endpoint(
    client: AsyncClient, seeded: Container, auth_headers: dict[str, str]
) -> None:
    weak = next(x for x in await seeded.disputes.list() if x.order_id == "ord_r004")
    r = await client.post(
        f"/disputes/{weak.id}/propose", json={"type": "submit"}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["type"] == "submit_dispute_evidence"

    r = await client.post(
        f"/disputes/{weak.id}/draft",
        json={"instruction": "mention the tracking number"},
        headers=auth_headers,
    )
    assert r.status_code == 202
    await seeded.tasks.drain()
    detail = (await client.get(f"/disputes/{weak.id}", headers=auth_headers)).json()
    assert detail["dispute"]["evidence_draft"]["instruction"] == "mention the tracking number"
    assert detail["order"]["id"] == "ord_r004"
    assert detail["actions"]
