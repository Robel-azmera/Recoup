"""Grounding check: every rebuttal sentence must be supported by the tool records it cites.

Two deterministic tests per sentence:
1. Citation validity: it cites at least one record id that a tool actually returned.
2. Fact containment: every checkable fact in the sentence (money amounts, dates, ids or
   tracking numbers, emails, URLs, quoted customer words) appears in the cited records.
   A sentence with a real citation but an invented detail is still unsupported.

Unsupported sentences block submission (see policy rules).
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from ..models import DraftClaim, DraftSentence, SourceRecord

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
RE_QUOTE = re.compile(r"[\"“]([^\"”]{8,})[\"”]")
RE_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?")
RE_DATE_LONG = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b")
RE_DATE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
RE_URL = re.compile(r"https?://[^\s\"”)]+")
RE_IDLIKE = re.compile(
    r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z_])[A-Za-z0-9][A-Za-z0-9_\-]{5,}\b"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _haystack(records: list[SourceRecord]) -> str:
    return _norm(
        " ".join(json.dumps({"id": r.id, "label": r.label, **r.data}, default=str) for r in records)
    )


def facts_in(text: str) -> list[tuple[str, list[str]]]:
    """[(fact as written, acceptable normalised forms)]."""
    out: list[tuple[str, list[str]]] = []
    stripped = text
    for m in RE_QUOTE.finditer(text):
        out.append((m.group(0), [_norm(m.group(1))]))
        stripped = stripped.replace(m.group(0), " ")
    for m in RE_URL.finditer(stripped):
        url = m.group(0).rstrip(".,")
        out.append((url, [url.lower()]))
        stripped = stripped.replace(m.group(0), " ")
    for m in RE_EMAIL.finditer(stripped):
        out.append((m.group(0), [m.group(0).lower()]))
        stripped = stripped.replace(m.group(0), " ")
    for m in RE_MONEY.finditer(stripped):
        whole = m.group(1).replace(",", "")
        cents = m.group(2) or "00"
        out.append((m.group(0), [f"${int(whole):,}.{cents}", f"{int(whole) * 100 + int(cents)}"]))
        stripped = stripped.replace(m.group(0), " ")
    for m in RE_DATE_LONG.finditer(stripped):
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
        out.append((m.group(0), [d.strftime("%Y-%m-%d")]))
        stripped = stripped.replace(m.group(0), " ")
    for m in RE_DATE_ISO.finditer(stripped):
        out.append((m.group(0), [m.group(1)]))
        stripped = stripped.replace(m.group(0), " ")
    for m in RE_IDLIKE.finditer(stripped):
        tok = m.group(0)
        out.append((tok, [tok.lower()]))
    return out


def check_claim(claim: DraftClaim, known: dict[str, SourceRecord]) -> tuple[list[str], str | None]:
    """Returns (valid source ids, unsupported reason or None)."""
    unknown = [s for s in claim.source_ids if s not in known]
    valid = [s for s in claim.source_ids if s in known]
    if not valid:
        if unknown:
            return [], f"cites unknown source(s): {', '.join(unknown)}"
        return [], "no citation"
    hay = _haystack([known[s] for s in valid])
    for written, forms in facts_in(claim.text):
        if not any(f in hay for f in forms):
            return valid, f"'{written}' is not in the cited source(s)"
    return valid, None


def ground(
    claims: list[DraftClaim], known: dict[str, SourceRecord], start: int = 1
) -> list[DraftSentence]:
    sentences: list[DraftSentence] = []
    for n, claim in enumerate(claims, start=start):
        ids, reason = check_claim(claim, known)
        sentences.append(
            DraftSentence(
                id=f"s{n}",
                field=claim.field,
                text=claim.text.strip(),
                source_ids=ids,
                author="agent",
                unsupported_reason=reason,
            )
        )
    return sentences
