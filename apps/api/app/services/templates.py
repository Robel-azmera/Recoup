"""Message templates (Telegram HTML + email). Facts only, all values escaped."""

from __future__ import annotations

from html import escape

from ..models import Dispute, PolicyCheck

CATEGORY = {
    "fraudulent": "Fraudulent",
    "product_not_received": "Product not received",
    "product_unacceptable": "Not as described",
    "duplicate": "Duplicate",
    "subscription_canceled": "Subscription cancelled",
    "credit_not_processed": "Credit not processed",
    "general": "General / inquiry",
}


def money(cents: int, currency: str = "usd") -> str:
    symbol = "$" if currency.lower() == "usd" else currency.upper() + " "
    return f"{symbol}{cents / 100:,.2f}"


def dispute_alert(d: Dispute, link: str) -> str:
    rec = {"fight": "fight", "accept": "accept the loss"}.get(
        d.recommendation.value if d.recommendation else "", "review"
    )
    found = [i.label for i in d.checklist if i.status.value == "found"][:4]
    missing = [i.label for i in d.checklist if i.status.value == "missing" and i.required]
    lines = [
        f"⚠️ <b>New dispute · {money(d.amount, d.currency)}</b>",
        f"{escape(CATEGORY.get(d.category.value, d.category.value))} · due <b>{d.due_by:%a %d %b %H:%M} UTC</b>",
        f"Win likelihood <b>{d.win_score if d.win_score is not None else '-'}/100</b>, the agent recommends <b>{rec}</b>.",
    ]
    if found:
        lines.append("✅ " + escape("; ".join(found)))
    if missing:
        lines.append("❌ Missing: " + escape("; ".join(missing)))
    unsupported = len(d.evidence_draft.unsupported_sentence_ids) if d.evidence_draft else 0
    if unsupported:
        lines.append(f"🚫 {unsupported} unsupported sentence(s): fix in the app before submitting")
    lines.append(
        f'<a href="{escape(link)}">Open the dispute workspace</a> · <code>{escape(d.id)}</code>'
    )
    return "\n".join(lines)


def confirm_text(d: Dispute, action_label: str, checks: list[PolicyCheck]) -> str:
    failed = [c for c in checks if not c.passed]
    lines = [
        f"<b>{escape(action_label)}</b> · {money(d.amount, d.currency)} · <code>{escape(d.id)}</code>"
    ]
    if failed:
        lines.append("🚫 Blocked by policy:")
        lines += [f"• {escape(c.name)}: {escape(c.detail or 'failed')}" for c in failed]
    else:
        lines.append(f"✅ All {len(checks)} policy checks pass. Confirm?")
    return "\n".join(lines)


def result_text(d: Dispute, action_label: str, status: str, by: str, error: str | None) -> str:
    icon = "✅" if status == "executed" else "🚫"
    out = f"{icon} <b>{escape(action_label)}</b> · {money(d.amount, d.currency)} · <code>{escape(d.id)}</code>\n{escape(status)} by {escape(by)}"
    if error:
        out += f"\n{escape(error)}"
    return out


def dispute_email(d: Dispute, link: str) -> tuple[str, str, str]:
    subject = f"New {CATEGORY.get(d.category.value, 'dispute').lower()} dispute: {money(d.amount, d.currency)}, due {d.due_by:%d %b}"
    text = (
        f"A {money(d.amount, d.currency)} dispute ({d.reason_code}) needs a response by {d.due_by:%Y-%m-%d %H:%M} UTC.\n"
        f"Win likelihood: {d.win_score}/100. Recommendation: {d.recommendation.value if d.recommendation else 'review'}.\n"
        f"Review and approve: {link}\n"
    )
    html = (
        f"<p>A <b>{money(d.amount, d.currency)}</b> dispute (<code>{escape(d.reason_code)}</code>) needs a response by "
        f"<b>{d.due_by:%Y-%m-%d %H:%M} UTC</b>.</p><p>Win likelihood <b>{d.win_score}/100</b>, recommendation "
        f"<b>{escape(d.recommendation.value if d.recommendation else 'review')}</b>.</p>"
        f'<p><a href="{escape(link)}">Review and approve in Recoup</a></p>'
    )
    return subject, html, text
