"""End-to-end tests against a real Shamela installation.

Skipped automatically when no library is present, so the suite still runs on a
machine (or CI runner) that has no Shamela installed.
"""

from __future__ import annotations

import pytest

from shamela_mcp.config import load_settings
from shamela_mcp.context import ServerContext
from shamela_mcp.discover import find_library

pytestmark = pytest.mark.integration

library, _ = find_library(load_settings().library_dir)
if library is None:
    pytest.skip("no Shamela library found on this machine", allow_module_level=True)


@pytest.fixture(scope="module")
def context() -> ServerContext:
    ctx = ServerContext(load_settings())
    yield ctx
    ctx.shutdown()


@pytest.fixture(scope="module")
def engine(context: ServerContext):
    return context.require_engine()


class TestLibrary:
    def test_catalogue_is_populated(self, context: ServerContext) -> None:
        totals = context.catalogue.totals()
        # Counts are a moving target as the user downloads books, so assert only
        # that the catalogue is real rather than pinning a number.
        assert totals["books"] > 100
        assert totals["downloaded"] > 0
        assert totals["categories"] > 10

    def test_categories_exclude_the_placeholder(self, context: ServerContext) -> None:
        names = [c.name for c in context.catalogue.categories()]
        assert "#" not in names
        assert any("فقه" in name for name in names)

    def test_engine_reports_a_live_index(self, engine) -> None:
        info = engine.health(force=True)
        assert info["page_docs"] > 1000
        assert info["page_generation"] is not None
        assert info["lucene_version"].startswith("10.")
        # Without this field, scope filters cannot be pushed into Lucene.
        assert info["book_field"] in ("book_key", "book")


class TestFoldingAgainstTheIndex:
    """The rules in normalize.py must match what the indexer actually did."""

    @pytest.mark.parametrize(
        ("present", "absent"),
        [
            ("بير", "بئر"),
            ("مكه", "مكة"),
            ("الامر", "الأمر"),
            ("علي", "على"),
            ("بن", "ابن"),
        ],
    )
    def test_folded_form_is_indexed_and_unfolded_form_is_not(
        self, context: ServerContext, present: str, absent: str
    ) -> None:
        result = context.bridge.probe(index="page", field="body", terms=[present, absent])
        frequencies = result["docfreqs"]
        assert frequencies[present] > 0, f"{present} should be indexed"
        assert frequencies[absent] == 0, f"{absent} should have been folded away"


class TestSearch:
    def test_phrase_search_finds_a_ubiquitous_phrase(self, engine) -> None:
        outcome = engine.search(query="الحمد لله", match_mode="phrase", limit=3)
        assert outcome.total_hits > 0
        assert outcome.passages

        for passage in outcome.passages:
            assert passage.text, "page text must not be empty"
            assert passage.citation.book_name
            assert passage.citation.page_id > 0
            assert passage.citation.formatted().startswith("[")

    def test_diacritized_text_is_returned_not_the_folded_form(self, engine) -> None:
        outcome = engine.search(query="الحمد لله", match_mode="phrase", limit=1)
        text = outcome.passages[0].text
        # Real Shamela pages are diacritized; a folded copy would have none.
        assert any(0x064B <= ord(ch) <= 0x0652 for ch in text)

    def test_scoped_search_pushes_the_filter_down(self, context: ServerContext, engine) -> None:
        category = context.catalogue.resolve_category("الفقه الشافعي")
        if category is None:
            pytest.skip("category not present in this library")
        book_ids = context.catalogue.category_book_ids([category.id])
        if not book_ids:
            pytest.skip("no downloaded books in that category")

        outcome = engine.search(
            query="الصلاة", book_ids=book_ids, limit=2, books_in_scope=len(book_ids)
        )
        assert outcome.total_hits_exact
        for passage in outcome.passages:
            assert passage.book_id in set(book_ids)

    def test_root_search_widens_the_result_set(self, engine) -> None:
        literal = engine.search(query="الطلاق", search_mode="exact", limit=1)
        rooted = engine.search(query="الطلاق", search_mode="root", limit=1)
        if rooted.field != "m_body":
            pytest.skip("no root recorded for this word in this library")
        assert rooted.total_hits >= literal.total_hits

    def test_non_arabic_query_is_refused_before_searching(self, engine) -> None:
        from shamela_mcp import errors

        with pytest.raises(errors.ShamelaError) as caught:
            engine.search(query="prayer rules")
        assert caught.value.code == errors.QUERY_NOT_ARABIC

    def test_nonsense_query_returns_zero_not_an_error(self, engine) -> None:
        outcome = engine.search(query="ززززقققق ككككثثثث", limit=2)
        assert outcome.total_hits == 0
        assert outcome.passages == []


class TestPaging:
    def test_second_page_is_disjoint_and_the_total_is_stable(self, engine) -> None:
        first = engine.search(query="الصلاة", limit=2)
        if not first.next_cursor:
            pytest.skip("not enough hits to page")

        second = engine.search(query="الصلاة", limit=2, cursor_token=first.next_cursor)
        assert second.total_hits == first.total_hits

        seen = {(p.book_id, p.page_id) for p in first.passages}
        again = {(p.book_id, p.page_id) for p in second.passages}
        assert not (seen & again), "a page must not be delivered twice"

    def test_cursor_from_a_different_query_is_rejected(self, engine) -> None:
        from shamela_mcp import errors

        first = engine.search(query="الصلاة", limit=1)
        if not first.next_cursor:
            pytest.skip("not enough hits to page")
        with pytest.raises(errors.ShamelaError) as caught:
            engine.search(query="الزكاة", limit=1, cursor_token=first.next_cursor)
        assert caught.value.code == errors.CURSOR_STALE


class TestReading:
    def test_page_with_context_returns_neighbours(self, engine) -> None:
        outcome = engine.search(query="الحمد لله", match_mode="phrase", limit=1)
        hit = outcome.passages[0]

        pages = engine.page_with_context(hit.book_id, hit.page_id, neighbors=1)
        assert any(p.page_id == hit.page_id for p in pages)
        assert 1 <= len(pages) <= 3
        for page in pages:
            assert page.citation.book_id == hit.book_id

    def test_citation_carries_a_chapter_chain_somewhere_in_the_library(
        self, context: ServerContext, engine
    ) -> None:
        # Not every page sits under a heading, so scan a handful for one that does.
        outcome = engine.search(query="باب الوضوء", match_mode="phrase", limit=5)
        if not outcome.passages:
            pytest.skip("phrase not present in this library")
        assert any(p.citation.chapter_path for p in outcome.passages)

    def test_missing_book_is_reported_clearly(self, engine) -> None:
        from shamela_mcp import errors

        with pytest.raises(errors.ShamelaError) as caught:
            engine.page_with_context(99_999_999, 1)
        assert caught.value.code == errors.BOOK_NOT_FOUND


class TestCatalogueLookups:
    def test_find_books_by_title(self, context: ServerContext) -> None:
        books = context.catalogue.find_books("صحيح البخاري", limit=5)
        if not books:
            pytest.skip("book not in this library")
        assert any("البخاري" in book.name for book in books)

    def test_find_books_by_author(self, context: ServerContext) -> None:
        books = context.catalogue.find_books("النووي", limit=5)
        assert books

    def test_category_resolution(self, context: ServerContext) -> None:
        by_name = context.catalogue.resolve_category("الفقه الشافعي")
        if by_name is None:
            pytest.skip("category not present")
        assert context.catalogue.resolve_category(str(by_name.id)) == by_name

    def test_unknown_category_offers_suggestions(self, context: ServerContext) -> None:
        assert context.catalogue.resolve_category("الفقه الجعفري") is None
        assert context.catalogue.closest_categories("الفقه الجعفري")


class TestRoots:
    def test_root_store_answers_for_a_common_word(self, context: ServerContext) -> None:
        if not context.roots.available:
            pytest.skip("no root store in this library")
        assert context.roots.roots("الطلاق")

    def test_original_spelling_is_the_key(self, context: ServerContext) -> None:
        if not context.roots.available:
            pytest.skip("no root store in this library")
        # The cache is keyed by original spelling: بئر yields بءر, while the folded
        # بير yields roots for an unrelated word.
        original = context.roots.roots("بئر")
        if not original:
            pytest.skip("word not analysed in this library")
        assert original != context.roots.roots("بير")
