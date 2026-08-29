"""The three search tools: whole library, chosen categories, and a single book.

Scope is expressed by tool *name* rather than by an optional argument, because a model
picks a name far more reliably than it fills in a parameter it was not prompted about.
The category list is written into the category tool's description at startup from the
user's own master.db, so the numbers a model is told to use are the numbers that
installation actually has.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from .. import notes as notes_mod
from ..config import DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT
from ..render import passage_dict, search_text
from .shared import clamp, reply, tool

_QUOTE_PAIRS = (("«", "»"), ('"', '"'), ("“", "”"), ("'", "'"))

QueryArg = Annotated[
    str,
    Field(description="نصّ البحث بالعربية. يستوي المشكول وغيره؛ ولو وُضع بين «» عُدّ عبارة متتابعة."),
]
MatchModeArg = Annotated[
    str,
    Field(description="phrase: عبارة متتابعة. all_terms: كل الكلمات في الصفحة (الافتراضي). any_terms: أيّ كلمة."),
]
SearchModeArg = Annotated[
    str,
    Field(description="exact: بحث حرفي (الافتراضي). root: بحث بالجذر يشمل المشتقات (الطلاق ← يطلق، مطلقة)."),
]
LimitArg = Annotated[
    int,
    Field(description=f"عدد المواضع في هذه الدفعة (1–{MAX_SEARCH_LIMIT}). كل موضع يعيد نصّ صفحته كاملًا.", ge=1, le=MAX_SEARCH_LIMIT),
]
CursorArg = Annotated[
    str | None,
    Field(description="مؤشر المتابعة من دفعة سابقة، لجلب الدفعة التالية من النتائج نفسها."),
]


def _unquote(query: str, match_mode: str) -> tuple[str, str]:
    """A quoted query means a phrase; honour that without a second argument."""
    text = (query or "").strip()
    for opening, closing in _QUOTE_PAIRS:
        if len(text) > 2 and text.startswith(opening) and text.endswith(closing):
            return text[1:-1].strip(), "phrase"
    return text, match_mode


def _run(
    context,
    *,
    query: str,
    match_mode: str,
    search_mode: str,
    limit: int,
    cursor: str | None,
    book_ids: list[int] | None,
    scope_label_ar: str,
    books_in_scope: int | None,
    extra: dict[str, Any] | None = None,
) -> CallToolResult:
    engine = context.require_engine()
    text, match_mode = _unquote(query, match_mode)

    outcome = engine.search(
        query=text,
        match_mode=match_mode,
        search_mode=search_mode,
        book_ids=book_ids,
        limit=limit,
        cursor_token=cursor,
        scope_label_ar=scope_label_ar,
        books_in_scope=books_in_scope,
    )

    notes = notes_mod.for_passages(has_citations=bool(outcome.passages))
    notes.extend(outcome.notes_ar)
    if outcome.passages:
        notes.add(
            "نصوص الصفحات معروضة كاملة ليتّضح السياق قبل الحكم على النقل، "
            "فاقرأ ما قبل الموضع وما بعده."
        )

    structured: dict[str, Any] = {
        "query": text,
        "match_mode": outcome.match_mode,
        "search_mode": outcome.search_mode,
        "field": outcome.field,
        "scope_ar": outcome.scope_label_ar,
        "books_in_scope": outcome.books_in_scope,
        "total_hits": outcome.total_hits,
        "total_hits_exact": outcome.total_hits_exact,
        "returned": len(outcome.passages),
        "has_more": outcome.has_more,
        "next_cursor": outcome.next_cursor,
        "results": [passage_dict(p) for p in outcome.passages],
        "notes_ar": notes.as_list(),
    }
    if extra:
        structured.update(extra)
    return reply(search_text(outcome, notes), structured)


def category_catalogue_text(context) -> str:
    """The installation's own category numbers, for the tool description."""
    try:
        categories = context.catalogue.categories()
    except Exception:
        return ""
    return "، ".join(f"{c.name}={c.id}" for c in categories)


def register(mcp, context) -> None:
    catalogue_line = category_catalogue_text(context)

    # ---------- whole library ----------

    @mcp.tool(
        name="shamela_search",
        description=(
            "بحث نصّي في كل كتب المكتبة الشاملة المنزَّلة على جهاز المستخدم (جميع الفنون، "
            "آلاف الكتب). استعمله حين لا يتّضح الفنّ الذي تقع فيه المسألة، أو حين يطلب "
            "المستخدم البحث في المكتبة كلها، أو لتتبّع مصطلح عبر الفنون المختلفة. "
            "أمّا إن كان السؤال في فنّ معروف فقدّم shamela_search_category فهو أدقّ وأسرع: "
            "أحكام العبادات والمعاملات ← أقسام الفقه، تفسير آية ← قسم التفسير، تخريج حديث "
            "← أقسام السنة. وإن سمّى المستخدم كتابًا بعينه فاستعمل shamela_search_book. "
            "كل موضع يُعاد بنصّ صفحته كاملًا ومعه إحالة جاهزة (الكتاب/الجزء/الصفحة/الباب/"
            "المؤلف) انقلها عقب كل اقتباس. النتائج على دفعات؛ مرّر cursor للمزيد."
        ),
    )
    @tool("shamela_search")
    def shamela_search(
        query: QueryArg,
        match_mode: MatchModeArg = "all_terms",
        search_mode: SearchModeArg = "exact",
        limit: LimitArg = DEFAULT_SEARCH_LIMIT,
        cursor: CursorArg = None,
    ) -> CallToolResult:
        totals = context.catalogue.totals()
        return _run(
            context,
            query=query,
            match_mode=match_mode,
            search_mode=search_mode,
            limit=clamp(limit, 1, MAX_SEARCH_LIMIT, DEFAULT_SEARCH_LIMIT),
            cursor=cursor,
            book_ids=None,
            scope_label_ar="المكتبة كلها",
            books_in_scope=totals["downloaded"],
        )

    # ---------- chosen categories ----------

    @mcp.tool(
        name="shamela_search_category",
        description=(
            "بحث نصّي داخل قسم أو أكثر من أقسام المكتبة الشاملة، وهو الخيار الأول "
            "للأسئلة العلمية: حصر النطاق في فنّ المسألة يرفع دقة النتائج ويُسقط المشترك "
            "اللفظي من الفنون الأخرى. مرّر في categories أرقام الأقسام أو أسماءها.\n"
            f"أقسام هذه المكتبة: {catalogue_line}.\n"
            "أمثلة على التوجيه: أحكام الصلاة أو الزكاة أو البيوع ← [14,15,16,17,18,19] "
            "(وأضف 22 للفتاوى)؛ مسألة في مذهب بعينه ← قسمه وحده (الشافعية ← [16])؛ "
            "تفسير آية أو معنى لفظة قرآنية ← [3] (وأضف 4)؛ تخريج حديث أو الحكم عليه أو "
            "شرحه ← [6,7,8] (وأضف 9,10 للعلل والمصطلح)؛ مسألة عقدية ← [1,2]؛ أصول الفقه "
            "والقواعد ← [11,12]؛ ترجمة عالِم أو وفاته ← [26]؛ سيرة نبوية ← [24]؛ معنى "
            "لغوي أو صرفي ← [29,30,31].\n"
            "كل موضع يُعاد بنصّ صفحته كاملًا ومعه إحالة جاهزة انقلها عقب كل اقتباس."
        ),
    )
    @tool("shamela_search_category")
    def shamela_search_category(
        query: QueryArg,
        categories: Annotated[
            list[str],
            Field(description="أرقام الأقسام أو أسماؤها العربية، مثل [\"16\"] أو [\"الفقه الشافعي\"]."),
        ],
        match_mode: MatchModeArg = "all_terms",
        search_mode: SearchModeArg = "exact",
        limit: LimitArg = DEFAULT_SEARCH_LIMIT,
        cursor: CursorArg = None,
    ) -> CallToolResult:
        resolved = context.resolve_categories(categories)
        book_ids = context.catalogue.category_book_ids([c.id for c in resolved])
        label = "، ".join(f"{c.name} ({c.id})" for c in resolved)
        return _run(
            context,
            query=query,
            match_mode=match_mode,
            search_mode=search_mode,
            limit=clamp(limit, 1, MAX_SEARCH_LIMIT, DEFAULT_SEARCH_LIMIT),
            cursor=cursor,
            book_ids=book_ids,
            scope_label_ar=label or "أقسام محدّدة",
            books_in_scope=len(book_ids),
            extra={
                "resolved_categories": [
                    {"id": c.id, "name": c.name, "downloaded_books": c.downloaded_books}
                    for c in resolved
                ]
            },
        )

    # ---------- one book ----------

    @mcp.tool(
        name="shamela_search_book",
        description=(
            "بحث نصّي داخل كتاب واحد بعينه. استعمله حين يسمّي المستخدم كتابًا "
            "(«ابحث في المغني»، «ماذا قال النووي في المجموع؟»). يتطلّب book_id الرقمي: "
            "إن لم تعرفه فاستدعِ أولًا shamela_find_books باسم الكتاب أو مؤلفه، فإن "
            "تعدّدت المطابقات (والشاملة فيها طبعات متعددة للكتاب الواحد) فاعرضها على "
            "المستخدم ليختار. كل موضع يُعاد بنصّ صفحته كاملًا ومعه إحالة جاهزة."
        ),
    )
    @tool("shamela_search_book")
    def shamela_search_book(
        query: QueryArg,
        book_id: Annotated[
            int, Field(description="معرّف الكتاب الرقمي، من shamela_find_books.")
        ],
        match_mode: MatchModeArg = "all_terms",
        search_mode: SearchModeArg = "exact",
        limit: LimitArg = DEFAULT_SEARCH_LIMIT,
        cursor: CursorArg = None,
    ) -> CallToolResult:
        book = context.require_downloaded_book(book_id)
        return _run(
            context,
            query=query,
            match_mode=match_mode,
            search_mode=search_mode,
            limit=clamp(limit, 1, MAX_SEARCH_LIMIT, DEFAULT_SEARCH_LIMIT),
            cursor=cursor,
            book_ids=[book.id],
            scope_label_ar=f"كتاب: {book.name}",
            books_in_scope=1,
            extra={"book": {"book_id": book.id, "book_name": book.name,
                            "author": book.author_name}},
        )
