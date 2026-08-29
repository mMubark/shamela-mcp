"""Folding rules and the offset map.

These are the highest-stakes tests in the project: if folding drifts from what
Shamela's indexer did, searches return nothing and nothing looks broken. The rules
asserted here are the ones ``scripts/probe_index.py`` verifies against a real index.
"""

from __future__ import annotations

import unicodedata

import pytest

from shamela_mcp.normalize import (
    fold,
    has_arabic,
    iter_token_spans,
    normalize_with_map,
    strip_marks,
    tokenize,
    tokenize_pairs,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # Hamza on ya folds to ya. The index stores بير, so a query for بئر must
        # become بير or it matches nothing at all.
        ("بئر", "بير"),
        ("المسائل", "المسايل"),
        ("قائم", "قايم"),
        # Ta marbuta folds to ha.
        ("مكة", "مكه"),
        ("صلاة", "صلاه"),
        # The alef family collapses.
        ("الأمر", "الامر"),
        ("إسلام", "اسلام"),
        ("آمن", "امن"),
        ("ٱلله", "الله"),
        # Alef maqsura folds to ya.
        ("على", "علي"),
        ("موسى", "موسي"),
        # Hamza on waw folds to waw.
        ("مؤمن", "مومن"),
        # Diacritics, tatweel, and Quranic marks are dropped.
        ("قَالَ", "قال"),
        ("الرَّحْمَٰنِ", "الرحمن"),
        ("مـــحمد", "محمد"),
        # Digits are unified.
        ("٢٥٦", "256"),
        ("۱۲۳", "123"),
        # Persian letters map onto their Arabic counterparts.
        ("گل", "كل"),
        ("پ", "ب"),
    ],
)
def test_fold_rules(source: str, expected: str) -> None:
    assert fold(source) == expected


def test_standalone_hamza_is_kept() -> None:
    # ء is a letter in its own right here, and the roots in S2.db use it.
    assert fold("ءبو") == "ءبو"
    assert "ء" in fold("جزء")


def test_fold_is_idempotent() -> None:
    text = "قَالَ النَّبِيُّ ﷺ: إنَّمَا الأَعْمَالُ بِالنِّيَّاتِ"
    once = fold(text)
    assert fold(once) == once


def test_zero_width_and_bidi_marks_are_dropped() -> None:
    assert fold("ا​ب‏ج‪d") == "ابجd"


def test_ibn_folds_only_as_a_whole_token() -> None:
    assert tokenize("ابن حجر") == ["بن", "حجر"]
    # A word that merely starts with those letters is untouched.
    assert tokenize("ابنه") == ["ابنه"]
    assert tokenize("بن") == ["بن"]


def test_tokenize_splits_on_punctuation_and_keeps_digits() -> None:
    assert tokenize("قال: (إنما الأعمال) ٣ مرات") == [
        "قال",
        "انما",
        "الاعمال",
        "3",
        "مرات",
    ]


def test_strip_marks_keeps_letters_but_drops_diacritics() -> None:
    # Root lookups need this: S2.db is keyed by original spelling, so بئر must stay
    # بئر here even though fold() would turn it into بير.
    assert strip_marks("بِئْر") == "بئر"
    assert strip_marks("مَكَّة") == "مكة"
    assert strip_marks("مـــحمد") == "محمد"


def test_tokenize_pairs_gives_aligned_folded_and_original_forms() -> None:
    pairs = tokenize_pairs("بِئْر مَكَّة")
    assert pairs == [("بير", "بئر"), ("مكه", "مكة")]


def test_has_arabic() -> None:
    assert has_arabic("صلاة")
    assert not has_arabic("prayer rules")
    assert not has_arabic("12345")
    assert has_arabic("prayer صلاة")


class TestOffsetMap:
    def test_map_has_a_sentinel_entry(self) -> None:
        source = "قَالَ النَّبِيُّ"
        folded, index_map = normalize_with_map(source)
        assert len(index_map) == len(folded) + 1
        # The sentinel makes any slice up to len(folded) safe.
        assert index_map[-1] == len(unicodedata.normalize("NFC", source))

    def test_slice_recovers_the_original_spelling(self) -> None:
        source = "إنَّمَا الأَعْمَالُ بِالنِّيَّاتِ"
        folded, index_map = normalize_with_map(source)
        composed = unicodedata.normalize("NFC", source)

        needle = "الاعمال"
        start = folded.index(needle)
        cut = composed[index_map[start] : index_map[start + len(needle)]]
        # The diacritics come back intact -- this is what lets a match found in
        # folded space be shown as the book actually prints it.
        assert cut == "الأَعْمَالُ"

    def test_every_offset_is_within_the_source(self) -> None:
        source = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ ٢٥٦"
        folded, index_map = normalize_with_map(source)
        composed = unicodedata.normalize("NFC", source)
        assert all(0 <= offset <= len(composed) for offset in index_map)
        assert index_map == sorted(index_map)

    def test_folded_text_matches_fold(self) -> None:
        source = "مَكَّة وبِئْر وعلى"
        folded, _ = normalize_with_map(source)
        assert folded == fold(source)

    def test_empty_input(self) -> None:
        folded, index_map = normalize_with_map("")
        assert folded == ""
        assert index_map == [0]


def test_iter_token_spans_reports_positions_in_folded_text() -> None:
    folded = fold("قال ابن حجر")
    spans = list(iter_token_spans(folded))
    assert [term for term, _, _ in spans] == ["قال", "بن", "حجر"]
    for _term, start, end in spans:
        # The span still points at the pre-token-fold text, so ابن keeps its width.
        assert folded[start:end] in ("قال", "ابن", "حجر")
