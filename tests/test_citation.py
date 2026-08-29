"""Citations, HTML rendering, and the discipline of not inventing numbers."""

from __future__ import annotations

import pytest

from shamela_mcp.citation import (
    FOOTNOTE_LABEL_AR,
    NUMBERING_NOTE_AR,
    Citation,
    build,
    heading_text,
    html_to_text,
)
from shamela_mcp.master import Book
from shamela_mcp.notes import Notes, for_passages


def make_book(**overrides) -> Book:
    defaults = dict(
        id=1681,
        name="صحيح البخاري",
        category_id=6,
        category_name="كتب السنة",
        author_name="محمد بن إسماعيل البخاري",
        author_death="256",
        author_death_year=256,
        coauthors=(),
        downloaded=True,
        on_disk_flag=True,
    )
    defaults.update(overrides)
    return Book(**defaults)


class TestFormatting:
    def test_full_citation_has_every_segment(self) -> None:
        citation = build(
            make_book(),
            145,
            part="2",
            printed_page=145,
            chapter_path=["كتاب الصلاة", "باب فضل صلاة الجماعة"],
        )
        assert citation.formatted() == (
            "[صحيح البخاري | ج2 | ص145 | باب فضل صلاة الجماعة | "
            "محمد بن إسماعيل البخاري (ت 256)]"
        )

    def test_only_the_deepest_chapter_appears_in_the_line(self) -> None:
        citation = build(
            make_book(), 145, part="2", printed_page=145,
            chapter_path=["كتاب الصلاة", "أبواب الجماعة", "باب فضل صلاة الجماعة"],
        )
        assert "باب فضل صلاة الجماعة" in citation.formatted()
        # The full chain stays available for the body of the result.
        assert citation.chapter_label == "كتاب الصلاة › أبواب الجماعة › باب فضل صلاة الجماعة"

    def test_missing_volume_is_omitted_not_guessed(self) -> None:
        citation = build(make_book(), 145, part=None, printed_page=145)
        assert "ج" not in citation.formatted().split("|")[1]
        assert "ص145" in citation.formatted()

    def test_missing_page_falls_back_to_the_shamela_locator(self) -> None:
        citation = build(make_book(), 704, part=None, printed_page=None)
        formatted = citation.formatted()
        # The internal id is labelled as such, never dressed up as a printed page.
        assert "موضع الشاملة رقم 704" in formatted
        assert "ص704" not in formatted

    def test_author_without_a_death_year(self) -> None:
        citation = build(
            make_book(author_death=None, author_death_year=None), 5, part="1", printed_page=5
        )
        assert citation.formatted().endswith("محمد بن إسماعيل البخاري]")

    def test_unnamed_author_is_dropped_from_the_line(self) -> None:
        citation = build(
            make_book(author_name=None, author_death=None), 5, part="1", printed_page=5
        )
        assert "None" not in citation.formatted()

    @pytest.mark.parametrize(
        ("part", "printed", "expected"),
        [
            ("2", 145, "ج2/ص145"),
            (None, 145, "ص145"),
            ("2", None, "ج2 (الصفحة غير مرقّمة)"),
            (None, None, "غير مرقّم"),
        ],
    )
    def test_location_label(self, part, printed, expected) -> None:
        assert build(make_book(), 9, part=part, printed_page=printed).location_ar() == expected

    def test_gaps_are_reported(self) -> None:
        gaps = build(make_book(), 9, part=None, printed_page=None).missing_ar()
        assert len(gaps) == 3
        assert build(
            make_book(), 9, part="1", printed_page=9, chapter_path=["باب"]
        ).missing_ar() == []

    def test_zero_page_is_treated_as_absent(self) -> None:
        # Shamela records an unpaginated page as 0; that is not "page zero".
        citation = Citation(
            book_id=1, book_name="ك", author=None, author_death=None, category="ق",
            page_id=3, part=None, printed_page=None,
        )
        assert "موضع الشاملة رقم 3" in citation.formatted()


class TestHtmlToText:
    def test_paragraphs_are_separated_by_a_blank_line(self) -> None:
        assert html_to_text("<p>سطر</p><p>آخر</p>") == "سطر\n\nآخر"

    def test_line_break_tags_become_single_newlines(self) -> None:
        assert html_to_text("سطر<br>آخر") == "سطر\nآخر"

    def test_inline_tags_are_stripped(self) -> None:
        assert html_to_text("قال <b>النبي</b> ﷺ") == "قال النبي ﷺ"

    def test_entities_are_decoded(self) -> None:
        assert html_to_text("&laquo;نص&raquo; &amp; &#1575;") == "«نص» & ا"

    def test_scripts_and_comments_are_removed(self) -> None:
        assert html_to_text("<!-- x -->أ<script>bad()</script>ب") == "أب"

    def test_blank_runs_are_capped(self) -> None:
        assert html_to_text("<p>أ</p><br><br><br><br><p>ب</p>") == "أ\n\nب"

    def test_empty_input(self) -> None:
        assert html_to_text(None) == ""
        assert html_to_text("") == ""


class TestHeadingText:
    def test_shamela_brackets_are_unwrapped(self) -> None:
        # Headings are stored bracketed; left alone they would nest inside the
        # bracketed citation line and read as a mistake.
        assert heading_text("[باب سجود السهو]") == "باب سجود السهو"

    def test_nested_brackets_are_unwrapped(self) -> None:
        assert heading_text("[[كتاب الصلاة]]") == "كتاب الصلاة"

    def test_internal_brackets_are_preserved(self) -> None:
        assert heading_text("باب [الوضوء] وأحكامه") == "باب [الوضوء] وأحكامه"

    def test_bare_brackets_are_left_alone(self) -> None:
        assert heading_text("[]") == "[]"


class TestNotes:
    def test_duplicates_collapse(self) -> None:
        notes = Notes()
        notes.add("واحد")
        notes.add("واحد")
        notes.add("اثنان")
        assert notes.as_list() == ["واحد", "اثنان"]

    def test_blank_notes_are_ignored(self) -> None:
        notes = Notes()
        notes.add("")
        notes.add(None)
        assert not notes

    def test_passage_notes_state_the_numbering_caveat_once(self) -> None:
        notes = for_passages()
        rendered = notes.render()
        assert rendered.count(NUMBERING_NOTE_AR) == 1

    def test_footnote_label_names_the_editor(self) -> None:
        assert "المحقِّق" in FOOTNOTE_LABEL_AR
