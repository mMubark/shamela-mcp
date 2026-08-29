"""Per-book databases: pagination and the heading tree.

These files hold no text -- only the volume/page numbering and the structure of the
table of contents. The heading *text* lives in the Lucene title index, and the page
text in the page index, so a citation is always assembled from both sources.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass

from .discover import Library
from .master import book_file_path, open_readonly

MAX_OPEN_BOOKS = 16
MAX_TOC_DEPTH = 32


@dataclass(frozen=True)
class PageRow:
    id: int
    part: str | None
    printed_page: int | None
    number: int | None


@dataclass(frozen=True)
class TitleRow:
    id: int
    page_id: int
    parent: int | None


class BookRepository:
    """Bounded pool of read-only per-book connections, with cached heading tables."""

    def __init__(self, library: Library) -> None:
        self.library = library
        self._lock = threading.RLock()
        self._connections: OrderedDict[int, sqlite3.Connection] = OrderedDict()
        self._titles: OrderedDict[int, list[TitleRow]] = OrderedDict()

    # ---------- connections ----------

    def exists(self, book_id: int) -> bool:
        return book_file_path(self.library, book_id).is_file()

    def _connection(self, book_id: int) -> sqlite3.Connection | None:
        with self._lock:
            existing = self._connections.get(book_id)
            if existing is not None:
                self._connections.move_to_end(book_id)
                return existing

            path = book_file_path(self.library, book_id)
            if not path.is_file():
                return None
            try:
                connection = open_readonly(path)
            except sqlite3.Error:
                return None

            self._connections[book_id] = connection
            while len(self._connections) > MAX_OPEN_BOOKS:
                _, evicted = self._connections.popitem(last=False)
                evicted.close()
            return connection

    # ---------- pages ----------

    def page(self, book_id: int, page_id: int) -> PageRow | None:
        connection = self._connection(book_id)
        if connection is None:
            return None
        row = connection.execute(
            "SELECT id, part, page, number FROM page WHERE id = ?", (page_id,)
        ).fetchone()
        return _page_row(row) if row else None

    def pages(self, book_id: int, page_ids: list[int]) -> dict[int, PageRow]:
        connection = self._connection(book_id)
        if connection is None or not page_ids:
            return {}
        out: dict[int, PageRow] = {}
        # Chunked to stay well under SQLite's variable limit on large batches.
        for start in range(0, len(page_ids), 400):
            chunk = page_ids[start : start + 400]
            placeholders = ",".join("?" * len(chunk))
            for row in connection.execute(
                f"SELECT id, part, page, number FROM page WHERE id IN ({placeholders})", chunk
            ):
                out[row["id"]] = _page_row(row)
        return out

    def page_bounds(self, book_id: int) -> tuple[int, int] | None:
        connection = self._connection(book_id)
        if connection is None:
            return None
        row = connection.execute("SELECT MIN(id) AS lo, MAX(id) AS hi FROM page").fetchone()
        if row is None or row["lo"] is None:
            return None
        return int(row["lo"]), int(row["hi"])

    def page_count(self, book_id: int) -> int:
        connection = self._connection(book_id)
        if connection is None:
            return 0
        row = connection.execute("SELECT COUNT(*) AS n FROM page").fetchone()
        return int(row["n"]) if row else 0

    def parts(self, book_id: int) -> list[str]:
        connection = self._connection(book_id)
        if connection is None:
            return []
        rows = connection.execute(
            "SELECT DISTINCT part FROM page WHERE part IS NOT NULL AND part <> '' ORDER BY id"
        )
        return [str(row["part"]) for row in rows]

    def neighbors(self, book_id: int, page_id: int, count: int) -> tuple[list[int], list[int]]:
        """Ids of the ``count`` pages before and after ``page_id``.

        Page ids are not dense -- gaps are normal -- so neighbours must be looked up
        rather than computed as ``page_id ± 1``.
        """
        connection = self._connection(book_id)
        if connection is None or count <= 0:
            return [], []
        before = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM page WHERE id < ? ORDER BY id DESC LIMIT ?", (page_id, count)
            )
        ]
        after = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM page WHERE id > ? ORDER BY id ASC LIMIT ?", (page_id, count)
            )
        ]
        return list(reversed(before)), after

    def resolve_printed(self, book_id: int, part: str | None, printed_page: int) -> int | None:
        """Map a printed volume/page to the internal page id."""
        connection = self._connection(book_id)
        if connection is None:
            return None
        if part:
            row = connection.execute(
                "SELECT id FROM page WHERE page = ? AND TRIM(part) = TRIM(?) ORDER BY id LIMIT 1",
                (printed_page, str(part)),
            ).fetchone()
            if row:
                return int(row["id"])
            return None
        row = connection.execute(
            "SELECT id FROM page WHERE page = ? ORDER BY id LIMIT 1", (printed_page,)
        ).fetchone()
        return int(row["id"]) if row else None

    # ---------- headings ----------

    def titles(self, book_id: int) -> list[TitleRow]:
        with self._lock:
            cached = self._titles.get(book_id)
            if cached is not None:
                self._titles.move_to_end(book_id)
                return cached

        connection = self._connection(book_id)
        rows: list[TitleRow] = []
        if connection is not None:
            try:
                for row in connection.execute(
                    "SELECT id, page, parent FROM title ORDER BY page, id"
                ):
                    parent = row["parent"]
                    rows.append(
                        TitleRow(
                            id=int(row["id"]),
                            page_id=int(row["page"]) if row["page"] is not None else 0,
                            parent=int(parent) if parent else None,
                        )
                    )
            except sqlite3.Error:
                rows = []

        with self._lock:
            self._titles[book_id] = rows
            while len(self._titles) > MAX_OPEN_BOOKS:
                self._titles.popitem(last=False)
        return rows

    def chapter_title_ids(self, book_id: int, page_id: int) -> list[int]:
        """The heading chain covering ``page_id``, outermost first.

        The deepest heading at or before the page is found first, then its ancestors
        are walked through ``parent``. Sibling projects stopped at the single nearest
        heading, which loses the "كتاب الصلاة › باب صلاة الجماعة" context a citation
        needs.
        """
        titles = self.titles(book_id)
        if not titles:
            return []

        by_id = {title.id: title for title in titles}

        current: TitleRow | None = None
        for title in titles:
            if title.page_id <= page_id:
                current = title
            else:
                break
        if current is None:
            return []

        chain: list[int] = []
        seen: set[int] = set()
        node: TitleRow | None = current
        while node is not None and node.id not in seen and len(chain) < MAX_TOC_DEPTH:
            seen.add(node.id)
            chain.append(node.id)
            node = by_id.get(node.parent) if node.parent else None
        chain.reverse()
        return chain

    def toc_entries(
        self, book_id: int, *, from_title: int | None = None, max_depth: int = 2
    ) -> list[tuple[TitleRow, int]]:
        """Heading rows paired with their depth, in reading order."""
        titles = self.titles(book_id)
        if not titles:
            return []

        by_id = {title.id: title for title in titles}
        children: dict[int | None, list[TitleRow]] = {}
        for title in titles:
            parent = title.parent if title.parent in by_id else None
            children.setdefault(parent, []).append(title)

        out: list[tuple[TitleRow, int]] = []

        def walk(parent: int | None, depth: int) -> None:
            if depth > max_depth:
                return
            for child in children.get(parent, []):
                out.append((child, depth))
                walk(child.id, depth + 1)

        if from_title is not None:
            root = by_id.get(from_title)
            if root is None:
                return []
            out.append((root, 1))
            walk(root.id, 2)
        else:
            walk(None, 1)
        return out

    def close(self) -> None:
        with self._lock:
            for connection in self._connections.values():
                connection.close()
            self._connections.clear()
            self._titles.clear()


def _page_row(row: sqlite3.Row) -> PageRow:
    part = row["part"]
    printed = row["page"]
    return PageRow(
        id=int(row["id"]),
        part=str(part).strip() if part not in (None, "") else None,
        printed_page=int(printed) if isinstance(printed, int) and printed > 0 else None,
        number=row["number"] if isinstance(row["number"], int) else None,
    )
