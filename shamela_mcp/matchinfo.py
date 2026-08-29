"""Why a page matched -- stated in Arabic, without touching the page text.

Page texts are returned whole: a scholar reading a fiqh argument needs the sentence
before the ruling and the qualification after it, and an excerpt window cannot know
where that boundary falls. So nothing here trims. This module only *explains* the
match, by locating the query terms in the folded copy of the text and reporting which
were found and where.
"""

from __future__ import annotations

from dataclasses import dataclass

from .normalize import fold, normalize_with_map, tokenize_folded


@dataclass(frozen=True)
class MatchEvidence:
    found_terms: tuple[str, ...]
    missing_terms: tuple[str, ...]
    in_body: bool
    in_footnote: bool
    phrase_found: bool
    reason_ar: str


def _terms_present(folded_text: str, groups: list[list[str]]) -> tuple[list[str], list[str]]:
    """Which query positions are represented in the text, and which are not."""
    tokens = set(tokenize_folded(folded_text))
    found: list[str] = []
    missing: list[str] = []
    for group in groups:
        hit = next((term for term in group if term in tokens), None)
        if hit is not None:
            found.append(hit)
        elif group:
            missing.append(group[0])
    return found, missing


def _phrase_present(folded_text: str, groups: list[list[str]]) -> bool:
    """True when the groups appear consecutively, allowing alternatives per position."""
    if not groups:
        return False
    tokens = tokenize_folded(folded_text)
    span = len(groups)
    if len(tokens) < span:
        return False
    for start in range(len(tokens) - span + 1):
        if all(tokens[start + offset] in set(group) for offset, group in enumerate(groups)):
            return True
    return False


def evaluate(
    *,
    body: str | None,
    footnote: str | None,
    groups: list[list[str]],
    match_mode: str,
    query: str,
    root_mode: bool,
) -> MatchEvidence:
    body_folded = fold(body or "")
    foot_folded = fold(footnote or "")

    body_found, body_missing = _terms_present(body_folded, groups)
    foot_found, _ = _terms_present(foot_folded, groups)

    in_body = bool(body_found)
    in_footnote = bool(foot_found) and not in_body

    if match_mode == "phrase":
        phrase = _phrase_present(body_folded, groups) or _phrase_present(foot_folded, groups)
    else:
        phrase = False

    found = tuple(body_found or foot_found)
    missing = tuple(body_missing)

    where = "الحاشية" if in_footnote else "الصفحة"
    if match_mode == "phrase" and phrase:
        reason = f"وردت العبارة «{query}» متتابعة في {where}."
    elif root_mode and found:
        reason = (
            f"وردت في {where} كلمات من جذور البحث ({len(found)} من {len(groups)}): "
            + "، ".join(found)
            + "."
        )
    elif found and not missing:
        reason = (
            f"وردت جميع كلمات البحث في {where} ({len(found)} من {len(groups)}): "
            + "، ".join(found)
            + "."
        )
    elif found:
        reason = (
            f"وردت في {where} {len(found)} من {len(groups)} من كلمات البحث: "
            + "، ".join(found)
            + "."
        )
    else:
        # Lucene matched on an indexed variant that the plain fold does not reproduce
        # (a root-stemmed or number-normalised form), so say so instead of implying
        # the page is irrelevant.
        reason = (
            "طابق الفهرس هذه الصفحة على صورة أخرى للكلمة (كالجذر أو صورة الرقم)، "
            "ولم تظهر الكلمة بنصّها الحرفي."
        )

    return MatchEvidence(
        found_terms=found,
        missing_terms=missing,
        in_body=in_body,
        in_footnote=in_footnote,
        phrase_found=phrase,
        reason_ar=reason,
    )


def locate(text: str, groups: list[list[str]]) -> int | None:
    """Offset in the original text where the first query term appears, if any.

    Used to point a reader at the relevant part of a long page. The offset is mapped
    back through the folding so it lands on the original, diacritics and all -- the
    text itself is never cut.
    """
    if not text or not groups:
        return None
    folded, index_map = normalize_with_map(text)
    wanted = {term for group in groups for term in group}

    from .normalize import iter_token_spans

    for term, start, _ in iter_token_spans(folded):
        if term in wanted and start < len(index_map):
            return index_map[start]
    return None
