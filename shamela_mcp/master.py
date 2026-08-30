"""The library catalogue: books, authors, and categories from master.db.

Read-only throughout. The catalogue is small enough (a few thousand rows) to keep
folded in memory for name matching, and is reloaded when the file changes so a book
downloaded mid-session becomes visible without a restart.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .discover import Library
from .normalize import fold

# Shamela reserves this category as a placeholder; it holds no books.
PLACEHOLDER_CATEGORY_NAME = "#"


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    order: int
    total_books: int
    downloaded_books: int


@dataclass(frozen=True)
class Book:
    id: int
    name: str
    category_id: int
    category_name: str
    author_name: str | None
    author_death: str | None
    author_death_year: int | None
    coauthors: tuple[str, ...]
    downloaded: bool
    on_disk_flag: bool

    @property
    def author_label(self) -> str:
        if not self.author_name:
            return "مؤلف غير مسمّى"
        if self.author_death:
            return f"{self.author_name} (ت {self.author_death})"
        return self.author_name


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open a SQLite file read-only. SQLite itself then rejects any write."""
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    return connection


def book_file_path(library: Library, book_id: int) -> Path:
    """Per-book database path. Shamela shards by the last three digits of the id."""
    shard = f"{book_id % 1000:03d}"
    return library.book_dir / shard / f"{book_id}.db"


class MasterCatalogue:
    def __init__(self, library: Library) -> None:
        self.library = library
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._loaded_mtime: float | None = None
        self._books: dict[int, Book] = {}
        self._categories: dict[int, Category] = {}
        self._search_rows: list[tuple[int, str, str]] = []  # (book_id, folded name, folded author)

    # ---------- loading ----------

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = open_readonly(self.library.master_db)
        return self._connection

    def _ensure_loaded(self) -> None:
        with self._lock:
            try:
                mtime = self.library.master_db.stat().st_mtime
            except OSError:
                mtime = None
            if self._books and mtime == self._loaded_mtime:
                return
            self._load()
            self._loaded_mtime = mtime

    def _load(self) -> None:
        connection = self._connect()

        authors: dict[int, sqlite3.Row] = {
            row["author_id"]: row
            for row in connection.execute(
                "SELECT author_id, author_name, death_number, death_text FROM author"
            )
        }
        categories_raw = {
            row["category_id"]: row
            for row in connection.execute(
                "SELECT category_id, category_name, category_order FROM category"
            )
        }

        coauthors: dict[int, list[str]] = {}
        for table in ("author_book", "coauthor_book"):
            try:
                rows = connection.execute(f"SELECT author_id, book_id FROM {table}")
            except sqlite3.Error:
                continue
            for row in rows:
                author = authors.get(row["author_id"])
                if author is None or not author["author_name"]:
                    continue
                coauthors.setdefault(row["book_id"], []).append(author["author_name"])

        books: dict[int, Book] = {}
        search_rows: list[tuple[int, str, str]] = []
        per_category_total: dict[int, int] = {}
        per_category_downloaded: dict[int, int] = {}

        for row in connection.execute(
            "SELECT book_id, book_name, book_category, main_author, major_ondisk FROM book"
        ):
            book_id = row["book_id"]
            category_id = row["book_category"] or 0
            category_row = categories_raw.get(category_id)
            author_row = authors.get(row["main_author"])

            author_name = author_row["author_name"] if author_row else None
            death_text = author_row["death_text"] if author_row else None
            death_number = author_row["death_number"] if author_row else None
            # Shamela stores "unknown" as a sentinel far in the future.
            death_year = (
                int(death_number)
                if isinstance(death_number, int) and 0 < death_number < 2000
                else None
            )

            primary = [author_name] if author_name else []
            extra = tuple(
                name for name in coauthors.get(book_id, []) if name and name != author_name
            )

            # A book counts as searchable only when its text file is actually present.
            on_disk_flag = bool(row["major_ondisk"])
            downloaded = on_disk_flag and book_file_path(self.library, book_id).is_file()

            books[book_id] = Book(
                id=book_id,
                name=row["book_name"] or f"كتاب {book_id}",
                category_id=category_id,
                category_name=(category_row["category_name"] if category_row else "غير مصنّف"),
                author_name=author_name,
                author_death=death_text or None,
                author_death_year=death_year,
                coauthors=extra,
                downloaded=downloaded,
                on_disk_flag=on_disk_flag,
            )

            per_category_total[category_id] = per_category_total.get(category_id, 0) + 1
            if downloaded:
                per_category_downloaded[category_id] = per_category_downloaded.get(category_id, 0) + 1

            search_rows.append(
                (book_id, fold(row["book_name"] or ""), fold(" ".join(primary + list(extra))))
            )

        categories: dict[int, Category] = {}
        for category_id, row in categories_raw.items():
            name = row["category_name"] or ""
            if name.strip() == PLACEHOLDER_CATEGORY_NAME:
                continue
            order = row["category_order"]
            categories[category_id] = Category(
                id=category_id,
                name=name,
                order=order if isinstance(order, int) else category_id,
                total_books=per_category_total.get(category_id, 0),
                downloaded_books=per_category_downloaded.get(category_id, 0),
            )

        self._books = books
        self._categories = categories
        self._search_rows = search_rows

    # ---------- queries ----------

    def categories(self) -> list[Category]:
        self._ensure_loaded()
        return sorted(self._categories.values(), key=lambda c: c.order)

    def category(self, category_id: int) -> Category | None:
        self._ensure_loaded()
        return self._categories.get(category_id)

    def book(self, book_id: int) -> Book | None:
        self._ensure_loaded()
        return self._books.get(book_id)

    def totals(self) -> dict[str, int]:
        self._ensure_loaded()
        downloaded = sum(1 for b in self._books.values() if b.downloaded)
        return {
            "books": len(self._books),
            "downloaded": downloaded,
            "categories": len(self._categories),
            "authors": len({b.author_name for b in self._books.values() if b.author_name}),
        }

    def category_book_ids(self, category_ids: list[int], downloaded_only: bool = True) -> list[int]:
        self._ensure_loaded()
        wanted = set(category_ids)
        return [
            book.id
            for book in self._books.values()
            if book.category_id in wanted and (book.downloaded or not downloaded_only)
        ]

    def downloaded_book_ids(self) -> list[int]:
        """Every book whose text is actually on disk -- the true whole-library scope."""
        self._ensure_loaded()
        return [book.id for book in self._books.values() if book.downloaded]

    def resolve_category(self, given: str) -> Category | None:
        """Accept a category number or an Arabic name, exact or a unique substring."""
        self._ensure_loaded()
        text = (given or "").strip()
        if not text:
            return None

        if text.isdigit():
            return self._categories.get(int(text))

        folded = fold(text)
        for category in self._categories.values():
            if fold(category.name) == folded:
                return category

        matches = [c for c in self._categories.values() if folded in fold(c.name)]
        if len(matches) == 1:
            return matches[0]
        return None

    def closest_categories(self, given: str, limit: int = 5) -> list[Category]:
        """Suggestions for an unresolved category name, best guesses first."""
        self._ensure_loaded()
        folded = fold((given or "").strip())
        if not folded:
            return self.categories()[:limit]

        scored: list[tuple[int, Category]] = []
        tokens = set(folded.split())
        for category in self._categories.values():
            name = fold(category.name)
            score = 0
            if folded and folded in name:
                score = 3
            elif tokens & set(name.split()):
                score = 2
            elif any(token[:3] and token[:3] in name for token in tokens):
                score = 1
            if score:
                scored.append((score, category))
        scored.sort(key=lambda pair: (-pair[0], pair[1].order))
        return [category for _, category in scored[:limit]]

    def find_books(
        self,
        query: str,
        *,
        category_ids: list[int] | None = None,
        downloaded_only: bool = False,
        limit: int = 20,
    ) -> list[Book]:
        """Rank books by title/author match, preferring downloaded ones."""
        self._ensure_loaded()
        folded = fold((query or "").strip())
        wanted = set(category_ids or [])

        results: list[tuple[int, int, Book]] = []
        for book_id, name, author in self._search_rows:
            book = self._books.get(book_id)
            if book is None:
                continue
            if wanted and book.category_id not in wanted:
                continue
            if downloaded_only and not book.downloaded:
                continue

            if not folded:
                rank = 0
            elif name == folded:
                rank = 5
            elif name.startswith(folded):
                rank = 4
            elif f" {folded}" in f" {name}":
                rank = 3
            elif folded in name:
                rank = 2
            elif folded in author:
                rank = 1
            else:
                continue
            results.append((rank, 1 if book.downloaded else 0, book))

        results.sort(key=lambda item: (-item[0], -item[1], len(item[2].name), item[2].name))
        return [book for _, _, book in results[:limit]]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
