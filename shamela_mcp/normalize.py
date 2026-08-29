"""Arabic folding that reproduces what Shamela's analyzer did at index time.

The index stores folded terms but the *original* text, so two representations are
always in play. Query terms are folded here and sent to Lucene as exact terms; the
text shown to the reader is never the folded form. ``normalize_with_map`` bridges
the two by recording, for every folded character, where it came from in the source.

The fold rules are validated empirically against the real index by ``scripts/
probe_index.py`` and by shamela_health -- notably ``ئ -> ي``, which a sibling project
left unfolded and thereby returned zero hits for any query containing it.
"""

from __future__ import annotations

import re
import unicodedata

# Invisible characters: zero-width, bidi controls, BOM.
_INVISIBLE = frozenset(
    [
        "​", "‌", "‍", "‎", "‏",
        "‪", "‫", "‬", "‭", "‮",
        "⁠", "⁡", "⁢", "⁣", "⁤",
        "﻿", "؜",
    ]
)


def _is_droppable(ch: str) -> bool:
    code = ord(ch)
    return (
        0x064B <= code <= 0x065F  # tashkeel and combining marks
        or code == 0x0670  # superscript alef
        or 0x06D6 <= code <= 0x06ED  # Quranic annotation marks
        or 0x08F0 <= code <= 0x08F3  # extended tanween
        or code == 0x0640  # tatweel
        or ch in _INVISIBLE
    )


_FOLD_MAP = {
    # alef family
    "آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا", "ٲ": "ا", "ٳ": "ا",
    # ya family -- ئ folds too; the index proves it (بئر is stored as بير)
    "ى": "ي", "ی": "ي", "ئ": "ي", "ۍ": "ي", "ې": "ي",
    # waw family
    "ؤ": "و", "ۇ": "و", "ۆ": "و", "ۈ": "و", "ٶ": "و",
    # ta marbuta
    "ة": "ه",
    # Persian/Urdu letters that appear in some texts
    "ک": "ك", "گ": "ك", "ڪ": "ك",
    "پ": "ب", "چ": "ج", "ژ": "ز", "ڤ": "ف", "ں": "ن", "ھ": "ه",
}

# Arabic-Indic and extended Arabic-Indic digits.
for _i in range(10):
    _FOLD_MAP[chr(0x0660 + _i)] = str(_i)
    _FOLD_MAP[chr(0x06F0 + _i)] = str(_i)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_ARABIC_LETTER_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def has_arabic(text: str) -> bool:
    """True when the text contains at least one Arabic letter."""
    return bool(_ARABIC_LETTER_RE.search(text or ""))


def fold(text: str) -> str:
    """Fold text to the form Shamela indexed in ``body``. Idempotent."""
    if not text:
        return ""
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", text):
        if _is_droppable(ch):
            continue
        out.append(_FOLD_MAP.get(ch, ch))
    return "".join(out)


def strip_marks(text: str) -> str:
    """Remove diacritics and invisibles but keep every letter as written.

    The morphology cache (``service/S2.db``) is keyed by the word in its original
    spelling: ``بئر`` yields the root ``بءر``, while the folded ``بير`` yields an
    unrelated set. Root lookups therefore need this weaker normalisation, not
    :func:`fold`.
    """
    if not text:
        return ""
    return "".join(
        ch for ch in unicodedata.normalize("NFC", text) if not _is_droppable(ch)
    )


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Fold ``text`` and record where each folded character came from.

    Returns ``(folded, index_map)`` where ``index_map[i]`` is the offset of folded
    character ``i`` inside the NFC-composed source, and ``index_map[len(folded)]``
    equals ``len(source)``. That sentinel makes ``source[index_map[a]:index_map[b]]``
    safe for any ``a <= b <= len(folded)``, so an excerpt located in folded space can
    always be cut from the original text with its diacritics intact.
    """
    source = unicodedata.normalize("NFC", text or "")
    folded: list[str] = []
    index_map: list[int] = []
    for position, ch in enumerate(source):
        if _is_droppable(ch):
            continue
        replacement = _FOLD_MAP.get(ch, ch)
        for _ in replacement:
            index_map.append(position)
        folded.append(replacement)
    index_map.append(len(source))
    return "".join(folded), index_map


def tokenize(text: str) -> list[str]:
    """Fold then split into index terms."""
    return [_fold_token(t) for t in _WORD_RE.findall(fold(text))]


def tokenize_pairs(text: str) -> list[tuple[str, str]]:
    """Split into ``(folded_term, original_spelling)`` pairs.

    Literal search needs the folded term; root lookup needs the original spelling.
    Both come from the same tokenisation so the two stay aligned -- folding maps
    characters one-for-one and never moves a word boundary.
    """
    stripped = strip_marks(text)
    pairs: list[tuple[str, str]] = []
    for match in _WORD_RE.finditer(stripped):
        original = match.group()
        pairs.append((_fold_token(fold(original)), original))
    return pairs


def tokenize_folded(folded_text: str) -> list[str]:
    """Split already-folded text into terms (no second fold pass)."""
    return [_fold_token(t) for t in _WORD_RE.findall(folded_text)]


def iter_token_spans(folded_text: str):
    """Yield ``(term, start, end)`` for each token in already-folded text."""
    for match in _WORD_RE.finditer(folded_text):
        yield _fold_token(match.group()), match.start(), match.end()


def _fold_token(token: str) -> str:
    # Shamela indexes the pervasive "ابن" as "بن"; applied per whole token only.
    return "بن" if token == "ابن" else token
