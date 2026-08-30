"""Arabic rendering of results.

The text block is what Claude reads and quotes from, so each passage carries its full
page text and a ready-made citation line directly beneath it. Handing over a citation
that is already correct is what keeps footnotes accurate: there is nothing left for the
model to infer or assemble.
"""

from __future__ import annotations

from . import counting
from .citation import FOOTNOTE_LABEL_AR
from .engine import CoverageOutcome, Passage, SearchOutcome
from .master import Book, Category
from .notes import Notes

DIVIDER = "─" * 46

MATCH_MODE_LABELS = {
    "phrase": "عبارة متتابعة",
    "all_terms": "كل الكلمات",
    "any_terms": "أيّ كلمة",
}
SEARCH_MODE_LABELS = {"exact": "بحث حرفي", "root": "بحث بالجذر"}


def passage_block(passage: Passage, index: int | None = None) -> str:
    citation = passage.citation
    label = f"── [{index}] {DIVIDER}" if index is not None else f"── {DIVIDER}"

    lines = [label]
    header = f"الكتاب: {citation.book_name}"
    if citation.author:
        header += f" — {citation.author}"
        if citation.author_death:
            header += f" (ت {citation.author_death})"
    header += f" — القسم: {citation.category}"
    lines.append(header)

    location = f"الموضع: {citation.location_ar()}"
    if citation.chapter_label:
        location += f" — الباب: {citation.chapter_label}"
    location += f" — (book_id={citation.book_id}، page_id={citation.page_id})"
    lines.append(location)

    lines.append(f"سبب المطابقة: {passage.match_reason_ar}")
    lines.append("")
    lines.append("نصّ الصفحة:")
    lines.append(passage.text or "(الصفحة خالية من النص في الفهرس)")

    if passage.footnote:
        lines.append("")
        lines.append(f"{FOOTNOTE_LABEL_AR}:")
        lines.append(passage.footnote)

    lines.append("")
    lines.append(f"الإحالة: {citation.formatted()}")

    gaps = citation.missing_ar()
    if gaps:
        lines.append("تنبيه على النقص: " + "؛ ".join(gaps) + ".")
    return "\n".join(lines)


def search_text(
    outcome: SearchOutcome, notes: Notes, *, window_start: int | None = None
) -> str:
    # Numbering continues across pages: a batch labelled 1-5 on every page hides how
    # little of a large result set has actually been read.
    window_start = window_start or outcome.window_start
    mode = MATCH_MODE_LABELS.get(outcome.match_mode, outcome.match_mode)
    kind = SEARCH_MODE_LABELS.get(outcome.search_mode, outcome.search_mode)

    header = [f"نتائج البحث عن «{outcome.query}» — النمط: {mode} ({kind})"]
    scope = f"النطاق: {outcome.scope_label_ar}"
    if outcome.books_in_scope is not None:
        scope += f" — عدد الكتب المبحوث فيها: {outcome.books_in_scope}"
    header.append(scope)

    if outcome.passages:
        window_end = window_start + len(outcome.passages) - 1
        total = f"{outcome.total_hits}" + ("" if outcome.total_hits_exact else " (تقريبًا)")
        line = f"إجمالي المواضع المطابقة: {total} — المعروض الآن: {window_start}–{window_end}"
        if outcome.books_in_batch:
            line += f" — عدد الكتب في هذه الدفعة: {outcome.books_in_batch}"
        header.append(line)
        if outcome.total_hits > window_end:
            header.append(
                "لم يُعرض إلا هذا القدر من المطابقات؛ فلا تحكم بأنك استوفيت الباب حتى "
                "تتابع بـ cursor أو تنظر توزيعها على الكتب بـ shamela_search_coverage."
            )
    else:
        header.append("إجمالي المواضع المطابقة: 0")

    body: list[str] = ["\n".join(header)]

    if not outcome.passages:
        body.append(zero_hits_text(outcome))
    else:
        for offset, passage in enumerate(outcome.passages, start=window_start):
            body.append(passage_block(passage, offset))

    if outcome.next_cursor:
        body.append(
            "للمزيد من النتائج: أعد الاستدعاء نفسه ومرّر cursor بالقيمة التالية:\n"
            f"{outcome.next_cursor}"
        )

    rendered_notes = notes.render()
    if rendered_notes:
        body.append(rendered_notes)
    return "\n\n".join(body)


def zero_hits_text(outcome: SearchOutcome) -> str:
    """Say plainly that nothing matched, and that this is not a ruling."""
    lines = [f"لا مطابقة نصّية لـ«{outcome.query}» في النطاق المبحوث."]
    if outcome.books_in_scope is not None:
        lines.append(
            f"وقد بُحث فعلًا في {counting.books(outcome.books_in_scope)} من الكتب المنزَّلة."
        )
    lines.append(
        "وهذا غياب مطابقة نصّية بهذه الصيغة، لا نفي لوجود المسألة أو الحكم في هذه الكتب."
    )
    lines.append(
        "جرّب: صيغة أخرى للمصطلح أو مرادفه، أو match_mode=any_terms، أو "
        "search_mode=root للبحث بالجذر، أو توسيع النطاق."
    )
    return "\n".join(lines)


def joined(lines: list[str], notes: Notes) -> str:
    """Body lines, with the response notes as a trailing block when there are any."""
    rendered = notes.render()
    body = "\n".join(lines)
    return f"{body}\n\n{rendered}" if rendered else body


def coverage_text(outcome: CoverageOutcome, notes: Notes) -> str:
    """Where the matches are, book by book -- the answer to "did it search them all?"."""
    mode = MATCH_MODE_LABELS.get(outcome.match_mode, outcome.match_mode)
    kind = SEARCH_MODE_LABELS.get(outcome.search_mode, outcome.search_mode)

    lines = [
        f"توزيع المطابقات على الكتب — «{outcome.query}» — النمط: {mode} ({kind})",
        f"النطاق: {outcome.scope_label_ar} — عدد الكتب المبحوث فيها: {outcome.books_in_scope}",
    ]

    if not outcome.books:
        lines.append(
            "لا مطابقة نصّية في أيّ كتاب من كتب النطاق "
            f"({counting.books(outcome.books_in_scope)} من الكتب المنزَّلة بُحث فيها كلِّها)."
        )
        lines.append(
            "وهذا غياب مطابقة نصّية بهذه الصيغة، لا نفي لوجود المسألة في هذه الكتب؛ "
            "جرّب صيغة أخرى أو مرادفًا أو match_mode=any_terms أو search_mode=root."
        )
        return joined(lines, notes)

    lines.append(
        f"الكتب التي فيها مطابقة: {outcome.books_with_matches} من {outcome.books_in_scope} "
        f"— إجمالي المواضع: {outcome.total_hits}"
    )
    lines.append("")
    lines.append(f"أكثر الكتب مطابقةً ({counting.books(len(outcome.books))}):")
    for book in outcome.books:
        lines.append(
            f"- {book.name_line()} — {counting.places(book.hits)} — القسم: {book.category_name} "
            f"— book_id={book.book_id}"
        )

    if outcome.truncated:
        rest_books = outcome.books_with_matches - len(outcome.books)
        rest_hits = outcome.total_hits - outcome.shown_hits
        lines.append("")
        lines.append(
            f"ولم تُعرض {counting.books(rest_books)} أخرى فيها "
            f"{counting.places(rest_hits)}؛ ارفع top لعرضها."
        )

    lines.append("")
    lines.append(
        "هذه أعداد المواضع لا نصوصها؛ لقراءة نصوص كتاب منها استعمل shamela_search_book "
        "بالاستعلام نفسه مع book_id."
    )

    return joined(lines, notes)


def coverage_dict(outcome: CoverageOutcome) -> dict:
    return {
        "query": outcome.query,
        "match_mode": outcome.match_mode,
        "search_mode": outcome.search_mode,
        "field": outcome.field,
        "scope_ar": outcome.scope_label_ar,
        "books_in_scope": outcome.books_in_scope,
        "books_with_matches": outcome.books_with_matches,
        "total_hits": outcome.total_hits,
        "shown_books": len(outcome.books),
        "shown_hits": outcome.shown_hits,
        "truncated": outcome.truncated,
        "books": [
            {
                "book_id": b.book_id,
                "book_name": b.book_name,
                "author": b.author_label,
                "category_name": b.category_name,
                "hits": b.hits,
            }
            for b in outcome.books
        ],
    }


def passage_dict(passage: Passage) -> dict:
    """Structured mirror of a passage, page text included.

    The text used to live only in the text block, on the reasoning that a client would
    show that block to the model and carrying the page twice would waste context. In
    practice a client that receives structuredContent hands the model *that*, so the
    scholar got citations with no text to quote and the model summarised from memory
    instead of transcribing the page -- the one failure this server exists to prevent.
    Carrying the text in both channels costs tokens; omitting it costs correctness.
    """
    data = passage.citation.as_dict()
    data.update(
        {
            "match_reason_ar": passage.match_reason_ar,
            "score": round(passage.score, 4),
            "text_original": passage.text,
            "text_length": len(passage.text),
            "footnote": passage.footnote or None,
            "has_footnote": bool(passage.footnote),
        }
    )
    return data


def book_line(book: Book) -> str:
    state = "منزَّل" if book.downloaded else "غير منزَّل"
    return (
        f"- {book.name} — {book.author_label} — القسم: {book.category_name} "
        f"— {state} — book_id={book.id}"
    )


def book_dict(book: Book) -> dict:
    return {
        "book_id": book.id,
        "book_name": book.name,
        "author": book.author_name,
        "author_death": book.author_death,
        "author_death_year": book.author_death_year,
        "category_id": book.category_id,
        "category_name": book.category_name,
        "downloaded": book.downloaded,
    }


def category_dict(category: Category) -> dict:
    return {
        "category_id": category.id,
        "name": category.name,
        "total_books": category.total_books,
        "downloaded_books": category.downloaded_books,
    }
