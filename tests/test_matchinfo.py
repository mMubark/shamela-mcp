"""Match evidence -- and the guarantee that page text is never trimmed."""

from __future__ import annotations

from shamela_mcp import matchinfo

DIACRITIZED = (
    "إنَّمَا الأَعْمَالُ بِالنِّيَّاتِ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى"
)


class TestEvidence:
    def test_all_terms_found(self) -> None:
        evidence = matchinfo.evaluate(
            body=DIACRITIZED,
            footnote="",
            groups=[["الاعمال"], ["بالنيات"]],
            match_mode="all_terms",
            query="الأعمال بالنيات",
            root_mode=False,
        )
        assert evidence.found_terms == ("الاعمال", "بالنيات")
        assert evidence.missing_terms == ()
        assert evidence.in_body
        assert "جميع كلمات البحث" in evidence.reason_ar

    def test_partial_match_is_reported_as_partial(self) -> None:
        evidence = matchinfo.evaluate(
            body=DIACRITIZED,
            footnote="",
            groups=[["الاعمال"], ["الوضوء"]],
            match_mode="all_terms",
            query="الأعمال الوضوء",
            root_mode=False,
        )
        assert evidence.found_terms == ("الاعمال",)
        assert evidence.missing_terms == ("الوضوء",)
        assert "1 من 2" in evidence.reason_ar

    def test_phrase_detected_only_when_consecutive(self) -> None:
        adjacent = matchinfo.evaluate(
            body=DIACRITIZED, footnote="", groups=[["الاعمال"], ["بالنيات"]],
            match_mode="phrase", query="الأعمال بالنيات", root_mode=False,
        )
        assert adjacent.phrase_found
        assert "متتابعة" in adjacent.reason_ar

        apart = matchinfo.evaluate(
            body=DIACRITIZED, footnote="", groups=[["انما"], ["نوي"]],
            match_mode="phrase", query="إنما نوى", root_mode=False,
        )
        assert not apart.phrase_found

    def test_phrase_accepts_alternatives_at_a_position(self) -> None:
        # Root phrases allow any root at each position.
        evidence = matchinfo.evaluate(
            body=DIACRITIZED, footnote="",
            groups=[["الاعمال", "عمل"], ["بالنيات", "نوي"]],
            match_mode="phrase", query="الأعمال بالنيات", root_mode=True,
        )
        assert evidence.phrase_found

    def test_footnote_only_match_is_flagged(self) -> None:
        evidence = matchinfo.evaluate(
            body="نص المصنف",
            footnote="قال المحقق: الوضوء",
            groups=[["الوضوء"]],
            match_mode="all_terms",
            query="الوضوء",
            root_mode=False,
        )
        assert evidence.in_footnote
        assert not evidence.in_body
        assert "الحاشية" in evidence.reason_ar

    def test_index_only_match_is_explained_not_denied(self) -> None:
        # Lucene can match on a stemmed variant the plain fold does not reproduce;
        # saying so is better than implying the page is irrelevant.
        evidence = matchinfo.evaluate(
            body="نص لا يحتوي الكلمة",
            footnote="",
            groups=[["طلق"]],
            match_mode="all_terms",
            query="الطلاق",
            root_mode=True,
        )
        assert evidence.found_terms == ()
        assert "صورة أخرى" in evidence.reason_ar

    def test_diacritics_do_not_prevent_a_match(self) -> None:
        evidence = matchinfo.evaluate(
            body="مَكَّةَ", footnote="", groups=[["مكه"]],
            match_mode="all_terms", query="مكة", root_mode=False,
        )
        assert evidence.found_terms == ("مكه",)

    def test_root_mode_wording(self) -> None:
        evidence = matchinfo.evaluate(
            body="يُطَلِّقُ امرأته", footnote="", groups=[["يطلق", "طلق"]],
            match_mode="all_terms", query="الطلاق", root_mode=True,
        )
        assert "جذور البحث" in evidence.reason_ar

    def test_empty_page_is_handled(self) -> None:
        evidence = matchinfo.evaluate(
            body=None, footnote=None, groups=[["كلمة"]],
            match_mode="all_terms", query="كلمة", root_mode=False,
        )
        assert evidence.found_terms == ()
        assert not evidence.in_body


class TestLocate:
    def test_offset_points_into_the_original_text(self) -> None:
        offset = matchinfo.locate(DIACRITIZED, [["الاعمال"]])
        assert offset is not None
        # The offset lands on the fully diacritized word, not a folded copy.
        assert DIACRITIZED[offset:].startswith("الأَعْمَالُ")

    def test_no_match_returns_none(self) -> None:
        assert matchinfo.locate(DIACRITIZED, [["الوضوء"]]) is None

    def test_empty_inputs(self) -> None:
        assert matchinfo.locate("", [["كلمة"]]) is None
        assert matchinfo.locate("نص", []) is None


def test_evidence_never_alters_the_text() -> None:
    """The module reports on text; it must not return a modified copy of it.

    Full pages are the product decision here -- a scholar needs the sentence before
    the ruling and the qualification after it -- so nothing in this path may trim.
    """
    body = DIACRITIZED
    matchinfo.evaluate(
        body=body, footnote="", groups=[["الاعمال"]],
        match_mode="all_terms", query="الأعمال", root_mode=False,
    )
    assert body == DIACRITIZED
    assert not hasattr(matchinfo, "excerpt")
    assert not hasattr(matchinfo, "snippet")
