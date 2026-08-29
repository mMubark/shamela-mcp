"""Exercise every tool once against the real library and print what came back.

Useful after any change to the tool layer: it shows the actual Arabic a scholar would
see, which is the part unit tests cannot judge.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shamela_mcp.server import build_server  # noqa: E402

PREVIEW = 700


async def run() -> int:
    mcp, context = build_server()
    if not context.has_library:
        print("No Shamela library found; run setup.bat or set SHAMELA_MCP_DIR.")
        return 1

    tools = await mcp.list_tools()
    print(f"tools registered: {len(tools)}")
    for tool in tools:
        print(f"  - {tool.name}")
    print()

    async def call(name: str, arguments: dict) -> tuple[str, dict]:
        result = await mcp.call_tool(name, arguments)
        text = "".join(getattr(item, "text", "") or "" for item in result.content)
        return text, (result.structuredContent or {})

    def show(title: str, text: str, structured: dict, keys: tuple[str, ...] = ()) -> None:
        print("=" * 70)
        print(title)
        print("=" * 70)
        body = text if len(text) <= PREVIEW else text[:PREVIEW] + "\n  […]"
        print(body)
        if keys:
            summary = {key: structured.get(key) for key in keys}
            print(f"  structured: {summary}")
        print()

    failures = 0

    text, structured = await call("shamela_health", {})
    show("shamela_health", text, structured, ("warnings_ar",))
    if structured.get("engine") is None:
        print("!! engine unavailable; the remaining checks will fail\n")
        failures += 1

    text, structured = await call("shamela_list_categories", {})
    show("shamela_list_categories", text, structured, ("totals",))

    text, structured = await call("shamela_find_books", {"query": "صحيح البخاري", "limit": 3})
    show("shamela_find_books", text, structured, ("returned",))
    books = structured.get("books") or []
    if not books:
        print("!! no books matched a title that should exist\n")
        failures += 1
    book_id = books[0]["book_id"] if books else None

    if book_id:
        text, structured = await call("shamela_book_info", {"book_id": book_id})
        show("shamela_book_info", text, structured, ("page_count", "parts"))

        text, structured = await call("shamela_get_toc", {"book_id": book_id, "depth": 1, "limit": 8})
        show("shamela_get_toc", text, structured, ("returned", "total_entries"))

    text, structured = await call(
        "shamela_search_category",
        {"query": "سجود السهو", "categories": ["الفقه الشافعي"], "match_mode": "phrase", "limit": 1},
    )
    show("shamela_search_category", text, structured, ("total_hits", "books_in_scope"))
    results = structured.get("results") or []
    if not results:
        print("!! a phrase that exists in the library returned nothing\n")
        failures += 1
    else:
        citation = results[0].get("citation") or ""
        print(f"  citation: {citation}\n")
        if not citation.startswith("["):
            failures += 1

    text, structured = await call(
        "shamela_search", {"query": "«الحمد لله رب العالمين»", "limit": 1}
    )
    show("shamela_search (quoted query becomes a phrase)", text, structured,
         ("match_mode", "total_hits"))
    if structured.get("match_mode") != "phrase":
        print("!! a quoted query should be treated as a phrase\n")
        failures += 1

    text, structured = await call(
        "shamela_search", {"query": "الطلاق", "search_mode": "root", "limit": 1}
    )
    show("shamela_search (root mode)", text, structured, ("field", "total_hits"))

    if results:
        hit = results[0]
        text, structured = await call(
            "shamela_get_page",
            {"book_id": hit["book_id"], "page_id": hit["page_id"], "neighbors": 1},
        )
        show("shamela_get_page", text, structured, ("returned", "next_page_id"))
        if not structured.get("pages"):
            failures += 1

    # Failure paths: each should answer in Arabic rather than raise.
    text, structured = await call("shamela_search", {"query": "prayer rules"})
    show("shamela_search (non-Arabic query)", text, structured, ("error",))
    if (structured.get("error") or {}).get("code") != "QUERY_NOT_ARABIC":
        failures += 1

    text, structured = await call(
        "shamela_search_category", {"query": "الصلاة", "categories": ["الفقه الجعفري"]}
    )
    show("shamela_search_category (unknown category)", text, structured, ("error",))
    if (structured.get("error") or {}).get("code") != "CATEGORY_UNKNOWN":
        failures += 1

    text, structured = await call("shamela_search_book", {"query": "الصلاة", "book_id": 99999999})
    show("shamela_search_book (unknown book)", text, structured, ("error",))
    if (structured.get("error") or {}).get("code") != "BOOK_NOT_FOUND":
        failures += 1

    text, structured = await call("shamela_guide", {"section": "workflow"})
    show("shamela_guide", text, structured, ("returned",))

    context.shutdown()
    print("=" * 70)
    print("PASS" if failures == 0 else f"FAIL ({failures} problem(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
