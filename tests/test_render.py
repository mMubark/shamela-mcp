"""Rendering guards, above all: the page text must actually reach the model.

A scholar asked about a fiqh issue and got citations with no quotable text; the model
summarised the ruling from memory instead of transcribing the page. The pages were
found and did hold text -- the server simply never delivered it, because the full text
lived only in the `content` text block while the client consumed `structuredContent`.
"""

from __future__ import annotations

from shamela_mcp import citation as citation_mod
from shamela_mcp.engine import Passage, SearchOutcome
from shamela_mcp.master import Book
from shamela_mcp.notes import Notes, for_passages
from shamela_mcp.render import passage_block, passage_dict, search_text

PAGE_TEXT = (
    "وإذا مات أحد المتناضلين قبل تمام الرشق انفسخت المناضلة في الباقي، "
    "ولم يستحق أحدهما على الآخر شيئا، لأن العقد قد ارتفع بموته."
)
FOOTNOTE_TEXT = "كذا في النسخة الخطية، وفي المطبوع: بموت أحدهما."


def make_passage(text: str = PAGE_TEXT, footnote: str = "") -> Passage:
    book = Book(
        id=21721,
        name="البيان في مذهب الإمام الشافعي",
        category_id=16,
        category_name="الفقه الشافعي",
        author_name="العمراني",
        author_death="558",
        author_death_year=558,
        coauthors=(),
        downloaded=True,
        on_disk_flag=True,
    )
    return Passage(
        book_id=book.id,
        page_id=3719,
        score=20.4451,
        citation=citation_mod.build(book, 3719, part="7", printed_page=468,
                                    chapter_path=["كتاب السبق والرمي", "مسألة: ما يبطل المناضلة"]),
        text=text,
        footnote=footnote,
        match_reason_ar="وردت جميع كلمات البحث في الصفحة (4 من 4).",
    )


class TestStructuredCarriesTheText:
    """The regression that reached a real user."""

    def test_structured_dict_contains_the_full_page_text(self) -> None:
        data = passage_dict(make_passage())
        assert data["text_original"] == PAGE_TEXT

    def test_text_is_not_truncated_in_the_structured_dict(self) -> None:
        long_page = "بسم الله. " + ("ونصّ طويل جدا يتكرر. " * 900)
        data = passage_dict(make_passage(text=long_page))
        assert data["text_original"] == long_page
        assert data["text_length"] == len(long_page)

    def test_text_length_agrees_with_the_text_it_reports(self) -> None:
        """A non-zero text_length beside a missing text is exactly what misled the model."""
        data = passage_dict(make_passage())
        assert data["text_length"] == len(data["text_original"])

    def test_footnote_reaches_the_structured_dict_too(self) -> None:
        data = passage_dict(make_passage(footnote=FOOTNOTE_TEXT))
        assert data["footnote"] == FOOTNOTE_TEXT
        assert data["has_footnote"] is True

    def test_absent_footnote_is_null_not_missing(self) -> None:
        data = passage_dict(make_passage())
        assert data["footnote"] is None
        assert data["has_footnote"] is False

    def test_citation_fields_survive_alongside_the_text(self) -> None:
        data = passage_dict(make_passage())
        assert data["book_name"] == "البيان في مذهب الإمام الشافعي"
        assert data["printed_page"] == 468
        assert data["part"] == "7"
        assert "citation" in data


class TestTextBlock:
    def test_text_block_still_carries_the_page(self) -> None:
        """Clients that read `content` must keep working."""
        assert PAGE_TEXT in passage_block(make_passage(), 1)

    def test_empty_page_says_so_rather_than_showing_nothing(self) -> None:
        assert "(الصفحة خالية من النص في الفهرس)" in passage_block(make_passage(text=""), 1)

    def test_footnote_is_labelled_as_the_editor_not_the_author(self) -> None:
        block = passage_block(make_passage(footnote=FOOTNOTE_TEXT), 1)
        assert citation_mod.FOOTNOTE_LABEL_AR in block


def make_outcome(passages: list[Passage], total: int | None = None) -> SearchOutcome:
    return SearchOutcome(
        query="مات أحد المتناضلين",
        match_mode="all_terms",
        search_mode="exact",
        field="body",
        groups=[["مات"], ["احد"], ["المتناضلين"]],
        total_hits=total if total is not None else len(passages),
        total_hits_exact=True,
        passages=passages,
        has_more=False,
        next_cursor=None,
        books_in_scope=419,
    )


class TestZeroHits:
    def test_zero_hits_is_not_stated_as_a_denial_of_the_ruling(self) -> None:
        text = search_text(make_outcome([]), Notes())
        assert "لا نفي لوجود المسألة أو الحكم" in text

    def test_zero_hits_suggests_a_concrete_next_attempt(self) -> None:
        text = search_text(make_outcome([]), Notes())
        assert "any_terms" in text and "root" in text

    def test_found_pages_render_their_text(self) -> None:
        passage = make_passage()
        text = search_text(make_outcome([passage]), for_passages())
        assert PAGE_TEXT in text


class TestBuildFingerprint:
    """A stale server process is indistinguishable from a fix that did not work.

    Claude Desktop starts the server once and keeps it alive, so an edit on disk stays
    dormant until the app is fully quit. This bit the user twice: the page-text fix was
    already on disk while the running process still withheld the text.
    """

    def test_running_build_matches_disk_in_a_fresh_process(self) -> None:
        from shamela_mcp import RUNNING_BUILD, build_id

        assert RUNNING_BUILD == build_id()

    def test_editing_a_source_file_changes_the_fingerprint(self, tmp_path) -> None:
        import pathlib

        from shamela_mcp import build_id

        target = pathlib.Path(__file__).parent.parent / "shamela_mcp" / "notes.py"
        before = target.read_bytes()
        baseline = build_id()
        try:
            target.write_bytes(before + b"\n# probe\n")
            assert build_id() != baseline
        finally:
            target.write_bytes(before)
        assert build_id() == baseline

    def test_fingerprint_is_short_and_stable(self) -> None:
        from shamela_mcp import build_id

        assert build_id() == build_id()
        assert len(build_id()) == 12
