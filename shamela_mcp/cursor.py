"""Pagination cursors bound to the index they were issued against.

A cursor carries the Lucene position to resume from, plus a fingerprint of the index
generation and a hash of the query. If Shamela rebuilds its indexes mid-session, or
the query changes, the cursor is rejected with an explanation instead of silently
resuming from a position that now points at different content.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from . import errors
from .config import CURSOR_VERSION, NORMALIZER_VERSION


@dataclass(frozen=True)
class Cursor:
    fingerprint: str
    query_hash: str
    after_doc: int
    after_score: float
    delivered: int
    total: int


def index_fingerprint(health: dict[str, Any]) -> str:
    """Identify the exact index state a cursor was issued against."""
    payload = json.dumps(
        {
            "normalizer": NORMALIZER_VERSION,
            "page_docs": health.get("page_docs"),
            "page_generation": health.get("page_generation"),
            "title_generation": health.get("title_generation"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def query_hash(
    *, field: str, mode: str, groups: list[list[str]], book_ids: list[str] | None
) -> str:
    payload = json.dumps(
        {
            "field": field,
            "mode": mode,
            "groups": groups,
            "books": sorted(book_ids) if book_ids else None,
            "normalizer": NORMALIZER_VERSION,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def encode(cursor: Cursor) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "fp": cursor.fingerprint,
        "qh": cursor.query_hash,
        "d": cursor.after_doc,
        "s": cursor.after_score,
        "n": cursor.delivered,
        "t": cursor.total,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(token: str, *, fingerprint: str, expected_query_hash: str) -> Cursor:
    """Decode a cursor, rejecting one issued for a different index or query."""
    text = (token or "").strip()
    if not text:
        raise errors.cursor_invalid("empty cursor")

    padding = "=" * (-len(text) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(text + padding))
    except (ValueError, TypeError) as exc:
        raise errors.cursor_invalid(f"cursor is not decodable: {exc}") from exc

    if not isinstance(payload, dict):
        raise errors.cursor_invalid("cursor payload is not an object")

    # Shape first: a payload missing fields is malformed, not merely out of date.
    # Only a well-formed cursor can be judged stale.
    missing = [key for key in ("v", "fp", "qh", "d", "s") if key not in payload]
    if missing:
        raise errors.cursor_invalid(f"cursor is missing fields: {', '.join(missing)}")

    if payload.get("v") != CURSOR_VERSION:
        raise errors.cursor_stale(
            "صدر هذا المؤشر من نسخة سابقة من الأداة.",
            f"cursor version {payload.get('v')} != {CURSOR_VERSION}",
        )
    if payload.get("fp") != fingerprint:
        raise errors.cursor_stale(
            "تغيّر فهرس المكتبة منذ بدأ هذا التصفح (لعل الشاملة نزّلت كتبًا أو أعادت الفهرسة).",
            "index fingerprint changed",
        )
    if payload.get("qh") != expected_query_hash:
        raise errors.cursor_stale(
            "تغيّر نصّ البحث أو نطاقه عن الذي صدر منه المؤشر.",
            "query hash changed",
        )

    try:
        return Cursor(
            fingerprint=str(payload["fp"]),
            query_hash=str(payload["qh"]),
            after_doc=int(payload["d"]),
            after_score=float(payload["s"]),
            delivered=int(payload.get("n", 0)),
            total=int(payload.get("t", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise errors.cursor_invalid(f"cursor is missing fields: {exc}") from exc
