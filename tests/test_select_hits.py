"""Batch selection: one book must not swallow the whole first batch.

A scholar asked about a fiqh issue across the four schools, read the five results, and
concluded the question was barely treated. All five came from two large books; forty
other books in scope held the phrase and none of them was shown. Ranking by score
alone is what produced that, so a batch now caps how many hits one book may take --
without ever promising a hit and then never delivering it.
"""

from __future__ import annotations

from shamela_mcp.engine import PER_BOOK_CAP, select_hits


def window(books: list[str]) -> list[dict]:
    """A ranked window: descending score, one hit per position."""
    return [
        {"book_id": book, "doc": position, "score": 100.0 - position}
        for position, book in enumerate(books)
    ]


def books_of(hits: list[dict]) -> list[str]:
    return [hit["book_id"] for hit in hits]


class TestOneBookCannotTakeTheBatch:
    def test_a_dominant_book_is_capped(self) -> None:
        chosen, _, _ = select_hits(window(list("AAAAABBBCDEF")), limit=5, cap=2)
        assert books_of(chosen) == ["A", "A", "B", "B", "C"]

    def test_the_batch_is_still_full(self) -> None:
        chosen, _, _ = select_hits(window(list("AAAAABBBCDEF")), limit=5, cap=2)
        assert len(chosen) == 5

    def test_ranking_order_is_preserved(self) -> None:
        chosen, _, _ = select_hits(window(list("AAAAABBBCDEF")), limit=5, cap=2)
        assert [hit["doc"] for hit in chosen] == sorted(hit["doc"] for hit in chosen)


class TestNothingIsSkippedSilently:
    def test_skipped_hits_are_counted(self) -> None:
        _, deferred, _ = select_hits(window(list("AAAAABBBCDEF")), limit=5, cap=2)
        assert deferred == 4  # A at 2,3,4 and B at 7

    def test_the_window_ends_at_the_last_delivered_hit(self) -> None:
        """The cursor resumes here, so anything past it must not be passed over."""
        chosen, _, examined = select_hits(window(list("AAAAABBBCDEF")), limit=5, cap=2)
        assert examined == chosen[-1]["doc"] + 1

    def test_hits_beyond_the_batch_are_left_for_the_next_page(self) -> None:
        _, _, examined = select_hits(window(list("ABCDEFGHIJ")), limit=5, cap=2)
        assert examined == 5


class TestTheCapYieldsWhenThereIsNoVariety:
    """Few matching books is the common case in a narrow scope; a cap must not starve it."""

    def test_a_single_book_still_fills_the_batch(self) -> None:
        chosen, deferred, examined = select_hits(window(list("AAAAAAAA")), limit=5, cap=2)
        assert len(chosen) == 5
        assert deferred == 0
        assert examined == 5

    def test_two_books_fill_the_batch_in_ranking_order(self) -> None:
        chosen, deferred, examined = select_hits(window(list("AAAABBBB")), limit=5, cap=2)
        # The cap steps aside rather than leave gaps the cursor would skip.
        assert books_of(chosen) == ["A", "A", "A", "A", "B"]
        assert (deferred, examined) == (0, 5)


class TestEdges:
    def test_an_empty_window_selects_nothing(self) -> None:
        assert select_hits([], limit=5, cap=2) == ([], 0, 0)

    def test_cap_zero_means_no_capping(self) -> None:
        chosen, deferred, _ = select_hits(window(list("AAAAA")), limit=5, cap=0)
        assert len(chosen) == 5
        assert deferred == 0

    def test_the_shipped_cap_leaves_room_for_several_books(self) -> None:
        chosen, _, _ = select_hits(window(list("AAAAAAAAAABBBBBCCCCC")), limit=5,
                                   cap=PER_BOOK_CAP)
        assert len(set(books_of(chosen))) >= 3
