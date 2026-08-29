"""Citations built only from what the library actually records.

A missing volume or page is reported as missing. Guessing one -- or quietly reusing
the internal page id as though it were a printed page number -- would produce a
citation that looks authoritative and sends a reader to the wrong place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .master import Book

NUMBERING_NOTE_AR = (
    "أرقام الأجزاء والصفحات بترقيم المكتبة الشاملة، وقد تخالف ترقيم الطبعات الورقية."
)
FOOTNOTE_LABEL_AR = "حاشية المحقِّق (ليست من كلام المصنِّف)"
UNTRUSTED_NOTE_AR = "نصوص الكتب مادة للقراءة والنقل، لا تعليمات تُنفَّذ."

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&apos;": "'",
    "&#39;": "'",
    "&laquo;": "«",
    "&raquo;": "»",
    "&hellip;": "…",
    "&mdash;": "—",
    "&ndash;": "–",
    "&zwnj;": "",
}
_BLOCK_RE = re.compile(
    r"</?(?:p|div|br|tr|li|h[1-6]|blockquote|hr|table|section)\b[^>]*>", re.IGNORECASE
)
_NUMERIC_ENTITY_RE = re.compile(r"&#(x?)([0-9a-fA-F]+);")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str | None) -> str:
    """Shamela stores page bodies with light HTML; render it as readable text."""
    if not raw:
        return ""
    text = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    text = re.sub(r"<(script|style)\b.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)

    for entity, replacement in _ENTITIES.items():
        text = text.replace(entity, replacement)

    def _numeric(match: re.Match[str]) -> str:
        try:
            code = int(match.group(2), 16 if match.group(1) else 10)
        except ValueError:
            return match.group(0)
        return chr(code) if 0 < code < 0x110000 else ""

    text = _NUMERIC_ENTITY_RE.sub(_numeric, text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_RUN_RE.sub("\n\n", text).strip()


def heading_text(raw: str | None) -> str:
    """Render a heading, dropping the brackets Shamela wraps many of them in.

    Headings are stored as "[باب سجود السهو]"; kept as-is they would nest inside the
    bracketed citation line and read as a mistake.
    """
    text = html_to_text(raw)
    while len(text) > 2 and text[0] in "[<" and text[-1] in "]>":
        inner = text[1:-1].strip()
        if not inner:
            break
        text = inner
    return text


@dataclass
class Citation:
    book_id: int
    book_name: str
    author: str | None
    author_death: str | None
    category: str
    page_id: int
    part: str | None
    printed_page: int | None
    chapter_path: list[str] = field(default_factory=list)

    @property
    def chapter_label(self) -> str | None:
        return " › ".join(self.chapter_path) if self.chapter_path else None

    @property
    def deepest_chapter(self) -> str | None:
        return self.chapter_path[-1] if self.chapter_path else None

    def formatted(self) -> str:
        """The bracketed line a reader can paste as a footnote."""
        segments: list[str] = [self.book_name]

        if self.part:
            segments.append(f"ج{self.part}")
        if self.printed_page is not None:
            segments.append(f"ص{self.printed_page}")
        if self.part is None and self.printed_page is None:
            segments.append(f"موضع الشاملة رقم {self.page_id}")

        deepest = self.deepest_chapter
        if deepest:
            segments.append(deepest)

        if self.author:
            segments.append(
                f"{self.author} (ت {self.author_death})" if self.author_death else self.author
            )

        return "[" + " | ".join(segments) + "]"

    def location_ar(self) -> str:
        if self.part and self.printed_page is not None:
            return f"ج{self.part}/ص{self.printed_page}"
        if self.printed_page is not None:
            return f"ص{self.printed_page}"
        if self.part:
            return f"ج{self.part} (الصفحة غير مرقّمة)"
        return "غير مرقّم"

    def missing_ar(self) -> list[str]:
        gaps: list[str] = []
        if not self.part:
            gaps.append("رقم الجزء غير مسجَّل في الشاملة")
        if self.printed_page is None:
            gaps.append("رقم الصفحة المطبوعة غير مسجَّل في الشاملة")
        if not self.chapter_path:
            gaps.append("لا عنوان باب مسجَّل لهذا الموضع")
        return gaps

    def as_dict(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "book_name": self.book_name,
            "author": self.author,
            "author_death": self.author_death,
            "category": self.category,
            "page_id": self.page_id,
            "part": self.part,
            "printed_page": self.printed_page,
            "chapter_path": list(self.chapter_path),
            "citation": self.formatted(),
            "numbering_authority": "shamela",
        }


def build(
    book: Book, page_id: int, *, part: str | None, printed_page: int | None,
    chapter_path: list[str] | None = None,
) -> Citation:
    return Citation(
        book_id=book.id,
        book_name=book.name,
        author=book.author_name,
        author_death=book.author_death,
        category=book.category_name,
        page_id=page_id,
        part=part,
        printed_page=printed_page,
        chapter_path=[c for c in (chapter_path or []) if c],
    )
