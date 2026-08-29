"""Response notes, collected once per response rather than repeated per result.

Caveats that apply to a whole response -- the numbering disclaimer, a root-search
fallback, the reminder that book text is source material and not instructions --
belong at the top of the response, stated once.
"""

from __future__ import annotations

from .citation import NUMBERING_NOTE_AR, UNTRUSTED_NOTE_AR

QUOTING_NOTE_AR = (
    "عند النقل في جوابك: انقل النص كما هو، وضع سطر «الإحالة» المرفق عقب كل نقل، "
    "ولا تُنشئ إحالة من عندك."
)
NOT_A_FATWA_AR = "هذه أداة بحث وتوثيق تنقل نصوص الكتب، وليست جهة إفتاء ولا ترجيح."


class Notes:
    """An ordered, de-duplicated list of Arabic notes."""

    def __init__(self, *initial: str) -> None:
        self._items: list[str] = []
        self.extend(initial)

    def add(self, note: str | None) -> None:
        text = (note or "").strip()
        if text and text not in self._items:
            self._items.append(text)

    def extend(self, notes) -> None:
        for note in notes or ():
            self.add(note)

    def as_list(self) -> list[str]:
        return list(self._items)

    def render(self, heading: str = "ملاحظات") -> str:
        if not self._items:
            return ""
        lines = [f"{heading}:"]
        lines.extend(f"- {item}" for item in self._items)
        return "\n".join(lines)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)


def for_passages(*, has_citations: bool = True) -> Notes:
    """The standing notes that accompany any response carrying book text."""
    notes = Notes()
    if has_citations:
        notes.add(NUMBERING_NOTE_AR)
        notes.add(QUOTING_NOTE_AR)
    notes.add(UNTRUSTED_NOTE_AR)
    return notes
