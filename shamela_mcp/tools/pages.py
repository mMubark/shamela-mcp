"""Reading tools: a page with its context, and a book's table of contents."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from .. import citation as citation_mod
from .. import errors
from .. import notes as notes_mod
from ..render import passage_block, passage_dict
from .shared import clamp, reply, tool


def register(mcp, context) -> None:

    @mcp.tool(
        name="shamela_get_page",
        description=(
            "يعرض نصّ صفحة كاملة من كتاب مع عزوها (الجزء/الصفحة/الباب/المؤلف)، ومعها "
            "صفحات قبلها وبعدها للسياق عبر neighbors. استعمله بعد أي بحث قبل النقل "
            "المطوَّل، ولمتابعة كلام المصنّف الذي انقطع بين صفحتين. يمكن طلب الصفحة "
            "بمعرّفها (page_id من نتيجة بحث أو من الفهرس) أو بترقيمها المطبوع "
            "(part مع page). حاشية المحقّق تُعرض مفصولة ولا تُنسب إلى المؤلف."
        ),
    )
    @tool("shamela_get_page")
    def shamela_get_page(
        book_id: Annotated[int, Field(description="معرّف الكتاب، من shamela_find_books.")],
        page_id: Annotated[
            int | None,
            Field(description="معرّف الصفحة داخل الكتاب (من نتيجة بحث أو من الفهرس)."),
        ] = None,
        part: Annotated[
            str | None, Field(description="رقم الجزء المطبوع، مع page.")
        ] = None,
        page: Annotated[
            int | None, Field(description="رقم الصفحة المطبوعة، مع part أو وحده.")
        ] = None,
        neighbors: Annotated[
            int, Field(description="عدد الصفحات المجاورة من كل جهة (0–3).", ge=0, le=3)
        ] = 1,
    ) -> CallToolResult:
        book = context.require_downloaded_book(book_id)
        engine = context.require_engine()

        resolved = page_id
        if resolved is None:
            if page is None:
                raise errors.bad_argument(
                    "لم يُحدَّد الموضع المطلوب.",
                    "مرّر page_id، أو مرّر page (ومعه part إن كان الكتاب مجزّءًا).",
                    "neither page_id nor page supplied",
                )
            resolved = context.books.resolve_printed(book_id, part, int(page))
            if resolved is None:
                where = f"ج{part}/ص{page}" if part else f"ص{page}"
                raise errors.bad_argument(
                    f"لا موضع بالترقيم المطبوع {where} في كتاب «{book.name}».",
                    "تأكّد من رقم الجزء، أو استعمل page_id من نتيجة بحث أو من "
                    "shamela_get_toc.",
                    f"printed page {part}/{page} not found in book {book_id}",
                )

        passages = engine.page_with_context(book_id, int(resolved), clamp(neighbors, 0, 3, 1))
        target = next((p for p in passages if p.page_id == int(resolved)), passages[0])

        before, after = context.books.neighbors(book_id, int(resolved), 1)
        notes = notes_mod.for_passages()
        if before or after:
            hint = []
            if before:
                hint.append(f"السابقة page_id={before[-1]}")
            if after:
                hint.append(f"التالية page_id={after[0]}")
            notes.add("للمتابعة: " + "، ".join(hint) + " عبر shamela_get_page.")

        header = [
            f"كتاب: {book.name} — {book.author_label}",
            f"الموضع المطلوب: {target.citation.location_ar()}"
            + (f" — الباب: {target.citation.chapter_label}" if target.citation.chapter_label else ""),
            f"الصفحات المعروضة: {len(passages)}",
        ]
        blocks = ["\n".join(header)]
        blocks.extend(passage_block(p) for p in passages)
        rendered = notes.render()
        if rendered:
            blocks.append(rendered)

        return reply(
            "\n\n".join(blocks),
            {
                "book_id": book.id,
                "book_name": book.name,
                "page_id": int(resolved),
                "returned": len(passages),
                "pages": [passage_dict(p) for p in passages],
                "previous_page_id": before[-1] if before else None,
                "next_page_id": after[0] if after else None,
                "notes_ar": notes.as_list(),
            },
        )

    @mcp.tool(
        name="shamela_get_toc",
        description=(
            "فهرس كتاب: شجرة الأبواب والفصول بمستوياتها، ومع كل عنوان معرّف الصفحة التي "
            "يبدأ عندها. استعمله للتصفّح المنهجي، أو للانتقال إلى باب بعينه ثم عرض نصّه "
            "بـ shamela_get_page. مرّر from_title لعرض شجرة باب واحد فقط."
        ),
    )
    @tool("shamela_get_toc")
    def shamela_get_toc(
        book_id: Annotated[int, Field(description="معرّف الكتاب، من shamela_find_books.")],
        depth: Annotated[
            int, Field(description="عدد مستويات الفهرس المعروضة (1–5).", ge=1, le=5)
        ] = 2,
        from_title: Annotated[
            int | None, Field(description="معرّف عنوان لعرض شجرته الفرعية وحدها.")
        ] = None,
        limit: Annotated[
            int, Field(description="أقصى عدد عناوين معروضة (1–500).", ge=1, le=500)
        ] = 200,
    ) -> CallToolResult:
        book = context.require_downloaded_book(book_id)
        max_depth = clamp(depth, 1, 5, 2)
        cap = clamp(limit, 1, 500, 200)

        entries = context.books.toc_entries(
            book_id, from_title=from_title, max_depth=max_depth
        )
        if not entries:
            if from_title is not None:
                raise errors.bad_argument(
                    f"لا عنوان بالمعرّف {from_title} في كتاب «{book.name}».",
                    "اطلب الفهرس بدون from_title أولًا لتعرف معرّفات العناوين.",
                    f"title {from_title} not in book {book_id}",
                )
            text = (
                f"لا فهرس مسجَّلًا لكتاب «{book.name}» في المكتبة الشاملة.\n"
                "اعرض صفحاته مباشرة عبر shamela_get_page، أو ابحث داخله بـ "
                "shamela_search_book."
            )
            return reply(text, {"book_id": book.id, "returned": 0, "entries": []})

        shown = entries[:cap]
        texts = _title_texts(context, book_id, [row.id for row, _ in shown])

        lines = [f"فهرس كتاب: {book.name} — {book.author_label}", ""]
        payload: list[dict[str, Any]] = []
        for row, level in shown:
            text = texts.get(row.id) or "(عنوان بلا نصّ في الفهرس)"
            lines.append("    " * (level - 1) + f"- {text} (page_id={row.page_id})")
            payload.append(
                {
                    "title_id": row.id,
                    "text": texts.get(row.id),
                    "page_id": row.page_id,
                    "level": level,
                    "parent_title_id": row.parent,
                }
            )

        truncated = len(entries) > len(shown)
        if truncated:
            lines.append("")
            lines.append(
                f"عُرض {len(shown)} عنوانًا من {len(entries)}. "
                "لعرض المزيد ارفع limit، أو اطلب شجرة باب بعينه عبر from_title."
            )
        lines.append("")
        lines.append(f"لعرض نصّ أيّ باب: shamela_get_page مع book_id={book.id} وpage_id للعنوان.")

        return reply(
            "\n".join(lines),
            {
                "book_id": book.id,
                "book_name": book.name,
                "depth": max_depth,
                "from_title": from_title,
                "returned": len(shown),
                "total_entries": len(entries),
                "truncated": truncated,
                "entries": payload,
            },
        )


def _title_texts(context, book_id: int, title_ids: list[int]) -> dict[int, str]:
    if not title_ids:
        return {}
    rows = context.bridge.get_titles([(book_id, title_ids)]).get(book_id, {})
    return {
        title_id: citation_mod.heading_text(row.get("body"))
        for title_id, row in rows.items()
    }
