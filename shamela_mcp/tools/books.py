"""Catalogue tools: categories, finding books, and a book's card.

These read only SQLite, so they keep working when the search engine cannot start --
which is what lets a user still learn what their library contains while diagnosing a
Java or index problem.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from ..render import book_dict, book_line, category_dict
from .shared import clamp, reply, tool


def register(mcp, context) -> None:

    @mcp.tool(
        name="shamela_list_categories",
        description=(
            "يسرد أقسام المكتبة الشاملة بأرقامها وأسمائها وعدد كتب كل قسم والمنزَّل منها. "
            "للاطلاع الحيّ على ما تحتويه مكتبة المستخدم فعلًا؛ وأرقام الأقسام مذكورة "
            "أيضًا في وصف shamela_search_category."
        ),
    )
    @tool("shamela_list_categories")
    def shamela_list_categories() -> CallToolResult:
        categories = context.catalogue.categories()
        totals = context.catalogue.totals()

        lines = [
            f"أقسام المكتبة: {len(categories)} قسمًا — "
            f"إجمالي الكتب: {totals['books']}، المنزَّل منها: {totals['downloaded']}.",
            "",
        ]
        for category in categories:
            lines.append(
                f"- {category.id}: {category.name} — "
                f"{category.downloaded_books} منزَّل من {category.total_books}"
            )
        return reply(
            "\n".join(lines),
            {
                "categories": [category_dict(c) for c in categories],
                "totals": totals,
            },
        )

    @mcp.tool(
        name="shamela_find_books",
        description=(
            "يبحث عن الكتب بأسمائها أو بأسماء مؤلفيها — لا في نصوصها. يعيد معرّف الكتاب "
            "(book_id) واسمه ومؤلفه ووفاته وقسمه وهل نصّه منزَّل. استعمله قبل "
            "shamela_search_book وshamela_get_page وshamela_get_toc، ولمعرفة الطبعة "
            "المتاحة من كتاب تعدّدت طبعاته في الشاملة."
        ),
    )
    @tool("shamela_find_books")
    def shamela_find_books(
        query: Annotated[
            str, Field(description="اسم الكتاب أو المؤلف أو جزء منه، بالعربية.")
        ],
        category: Annotated[
            str | None, Field(description="حصر النتائج في قسم واحد (رقمه أو اسمه).")
        ] = None,
        downloaded_only: Annotated[
            bool, Field(description="الاقتصار على الكتب المنزَّلة نصوصها.")
        ] = False,
        limit: Annotated[int, Field(description="عدد النتائج (1–50).", ge=1, le=50)] = 20,
    ) -> CallToolResult:
        category_ids: list[int] | None = None
        resolved_category = None
        if category:
            resolved_category = context.resolve_categories([category])[0]
            category_ids = [resolved_category.id]

        books = context.catalogue.find_books(
            query,
            category_ids=category_ids,
            downloaded_only=downloaded_only,
            limit=clamp(limit, 1, 50, 20),
        )

        if not books:
            scope = f" في قسم {resolved_category.name}" if resolved_category else ""
            text = (
                f"لا كتاب يطابق «{query}»{scope} في فهرس هذه المكتبة.\n"
                "جرّب جزءًا من الاسم فقط، أو اسم المؤلف، أو استعرض الأقسام عبر "
                "shamela_list_categories."
            )
            return reply(text, {"query": query, "returned": 0, "books": []})

        lines = [f"الكتب المطابقة لـ«{query}»: {len(books)}", ""]
        lines.extend(book_line(book) for book in books)
        missing = [book for book in books if not book.downloaded]
        if missing:
            lines.append("")
            lines.append(
                "الكتب غير المنزَّلة لا يمكن البحث في نصوصها؛ نزّلها من تطبيق المكتبة "
                "الشاملة أولًا."
            )
        return reply(
            "\n".join(lines),
            {
                "query": query,
                "category": category_dict(resolved_category) if resolved_category else None,
                "returned": len(books),
                "books": [book_dict(book) for book in books],
            },
        )

    @mcp.tool(
        name="shamela_book_info",
        description=(
            "بطاقة كتاب: الاسم، المؤلف ووفاته، المشاركون في التأليف، القسم، عدد الأجزاء "
            "والصفحات، حالة التنزيل، ومقدّمة من عناوين فهرسه. استعمله للتحقّق من هوية "
            "الكتاب وطبعته قبل النقل عنه."
        ),
    )
    @tool("shamela_book_info")
    def shamela_book_info(
        book_id: Annotated[int, Field(description="معرّف الكتاب، من shamela_find_books.")],
    ) -> CallToolResult:
        book = context.require_book(book_id)
        downloaded = context.books.exists(book_id)

        page_count = context.books.page_count(book_id) if downloaded else 0
        bounds = context.books.page_bounds(book_id) if downloaded else None
        parts = context.books.parts(book_id) if downloaded else []

        lines = [
            f"الكتاب: {book.name}",
            f"المؤلف: {book.author_label}",
        ]
        if book.coauthors:
            lines.append("مشاركون في التأليف: " + "، ".join(book.coauthors))
        lines.append(f"القسم: {book.category_name} (رقم {book.category_id})")
        lines.append(f"معرّف الكتاب: {book.id}")

        if downloaded:
            lines.append(
                f"عدد الصفحات في الشاملة: {page_count}"
                + (f" (معرّفات الصفحات {bounds[0]}–{bounds[1]})" if bounds else "")
            )
            lines.append(
                f"الأجزاء: {len(parts)}" + (f" — {'، '.join(parts[:12])}" if parts else "")
            )
        else:
            lines.append(
                "حالة النصّ: غير منزَّل — لا يمكن البحث فيه ولا عرض صفحاته. "
                "نزّله من تطبيق المكتبة الشاملة."
            )

        preview: list[dict[str, Any]] = []
        if downloaded:
            entries = context.books.toc_entries(book_id, max_depth=1)[:15]
            if entries:
                lines.append("")
                lines.append("من عناوين الفهرس:")
                titles = _title_texts(context, book_id, [row.id for row, _ in entries])
                for row, _ in entries:
                    text = titles.get(row.id, "")
                    if text:
                        lines.append(f"- {text} (page_id={row.page_id})")
                        preview.append(
                            {"title_id": row.id, "text": text, "page_id": row.page_id}
                        )
                lines.append("")
                lines.append("الفهرس كاملًا: shamela_get_toc.")

        return reply(
            "\n".join(lines),
            {
                **book_dict(book),
                "coauthors": list(book.coauthors),
                "page_count": page_count,
                "page_id_range": list(bounds) if bounds else None,
                "parts": parts,
                "toc_preview": preview,
            },
        )


def _title_texts(context, book_id: int, title_ids: list[int]) -> dict[int, str]:
    """Heading texts for a set of title ids; empty when the engine is unavailable."""
    if not title_ids:
        return {}
    from .. import citation as citation_mod
    from .. import errors

    try:
        rows = context.bridge.get_titles([(book_id, title_ids)]).get(book_id, {})
    except errors.ShamelaError:
        # Headings live in the Lucene index; without it the card still stands.
        return {}
    return {
        title_id: citation_mod.heading_text(row.get("body"))
        for title_id, row in rows.items()
    }
