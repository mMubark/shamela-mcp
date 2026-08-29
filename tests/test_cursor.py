"""Cursors: resume where we left off, or say clearly why we cannot."""

from __future__ import annotations

import base64
import json

import pytest

from shamela_mcp import cursor as cursor_mod
from shamela_mcp import errors

HEALTH = {
    "page_docs": 7_605_947,
    "page_generation": 29_206,
    "title_generation": 30_517,
}


def make_cursor(fingerprint: str, query_hash: str) -> str:
    return cursor_mod.encode(
        cursor_mod.Cursor(
            fingerprint=fingerprint,
            query_hash=query_hash,
            after_doc=1234,
            after_score=7.5,
            delivered=5,
            total=835,
        )
    )


class TestFingerprint:
    def test_stable_for_the_same_index_state(self) -> None:
        assert cursor_mod.index_fingerprint(HEALTH) == cursor_mod.index_fingerprint(dict(HEALTH))

    def test_changes_when_the_index_is_rebuilt(self) -> None:
        rebuilt = {**HEALTH, "page_generation": HEALTH["page_generation"] + 1}
        assert cursor_mod.index_fingerprint(rebuilt) != cursor_mod.index_fingerprint(HEALTH)

    def test_changes_when_documents_are_added(self) -> None:
        grown = {**HEALTH, "page_docs": HEALTH["page_docs"] + 1000}
        assert cursor_mod.index_fingerprint(grown) != cursor_mod.index_fingerprint(HEALTH)


class TestQueryHash:
    def test_same_query_same_hash(self) -> None:
        args = dict(field="body", mode="phrase", groups=[["سجود"], ["السهو"]], book_ids=["1"])
        assert cursor_mod.query_hash(**args) == cursor_mod.query_hash(**args)

    def test_scope_order_does_not_matter(self) -> None:
        first = cursor_mod.query_hash(
            field="body", mode="phrase", groups=[["أ"]], book_ids=["2", "1"]
        )
        second = cursor_mod.query_hash(
            field="body", mode="phrase", groups=[["أ"]], book_ids=["1", "2"]
        )
        assert first == second

    def test_different_terms_differ(self) -> None:
        first = cursor_mod.query_hash(field="body", mode="phrase", groups=[["أ"]], book_ids=None)
        second = cursor_mod.query_hash(field="body", mode="phrase", groups=[["ب"]], book_ids=None)
        assert first != second

    def test_field_is_part_of_the_identity(self) -> None:
        # A root search and a literal search are different queries even with the
        # same terms, so their cursors must not be interchangeable.
        literal = cursor_mod.query_hash(field="body", mode="all_terms", groups=[["طلق"]], book_ids=None)
        root = cursor_mod.query_hash(field="m_body", mode="all_terms", groups=[["طلق"]], book_ids=None)
        assert literal != root


class TestRoundTrip:
    def test_decode_returns_what_was_encoded(self) -> None:
        fingerprint = cursor_mod.index_fingerprint(HEALTH)
        token = make_cursor(fingerprint, "abc123")
        decoded = cursor_mod.decode(token, fingerprint=fingerprint, expected_query_hash="abc123")
        assert decoded.after_doc == 1234
        assert decoded.after_score == pytest.approx(7.5)
        assert decoded.delivered == 5
        # The total rides along so later batches need not recount.
        assert decoded.total == 835

    def test_token_is_url_safe_and_unpadded(self) -> None:
        token = make_cursor("fp", "qh")
        assert "=" not in token
        assert "+" not in token and "/" not in token


class TestRejection:
    def test_stale_when_the_index_changed(self) -> None:
        token = make_cursor(cursor_mod.index_fingerprint(HEALTH), "qh")
        rebuilt = cursor_mod.index_fingerprint({**HEALTH, "page_generation": 99})
        with pytest.raises(errors.ShamelaError) as caught:
            cursor_mod.decode(token, fingerprint=rebuilt, expected_query_hash="qh")
        assert caught.value.code == errors.CURSOR_STALE
        # The Arabic message must name the cause, not just the failure.
        assert "الفهرس" in caught.value.message_ar

    def test_stale_when_the_query_changed(self) -> None:
        fingerprint = cursor_mod.index_fingerprint(HEALTH)
        token = make_cursor(fingerprint, "original")
        with pytest.raises(errors.ShamelaError) as caught:
            cursor_mod.decode(token, fingerprint=fingerprint, expected_query_hash="different")
        assert caught.value.code == errors.CURSOR_STALE

    def test_stale_for_an_older_cursor_version(self) -> None:
        payload = {"v": 0, "fp": "fp", "qh": "qh", "d": 1, "s": 1.0, "n": 1, "t": 1}
        token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        with pytest.raises(errors.ShamelaError) as caught:
            cursor_mod.decode(token, fingerprint="fp", expected_query_hash="qh")
        assert caught.value.code == errors.CURSOR_STALE

    @pytest.mark.parametrize("token", ["", "   ", "not-base64!!", "e30", "W10"])
    def test_invalid_tokens(self, token: str) -> None:
        with pytest.raises(errors.ShamelaError) as caught:
            cursor_mod.decode(token, fingerprint="fp", expected_query_hash="qh")
        assert caught.value.code == errors.CURSOR_INVALID

    def test_missing_fields(self) -> None:
        payload = {"v": cursor_mod.CURSOR_VERSION, "fp": "fp", "qh": "qh"}
        token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        with pytest.raises(errors.ShamelaError) as caught:
            cursor_mod.decode(token, fingerprint="fp", expected_query_hash="qh")
        assert caught.value.code == errors.CURSOR_INVALID

    def test_errors_tell_the_caller_what_to_do(self) -> None:
        token = make_cursor("other", "qh")
        with pytest.raises(errors.ShamelaError) as caught:
            cursor_mod.decode(token, fingerprint="fp", expected_query_hash="qh")
        assert "أعد" in caught.value.next_step_ar
