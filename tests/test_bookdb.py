"""Per-book pagination and the heading chain, against synthetic databases.

The heading chain is the part worth guarding: sibling projects reported only the
single nearest heading, losing the "كتاب الصلاة › باب صلاة الجماعة" context that makes
a citation locatable in a printed copy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shamela_mcp.bookdb import BookRepository
from shamela_mcp.discover import Library


def make_library(tmp_path: Path) -> Library:
    database = tmp_path / "database"
    (database / "book" / "042").mkdir(parents=True)
    (database / "store").mkdir()
    (database / "service").mkdir()
    (tmp_path / "app").mkdir()
    (database / "master.db").touch()
    return Library(
        root=tmp_path,
        database_dir=database,
        app_dir=tmp_path / "app",
        master_db=database / "master.db",
        store_dir=database / "store",
        book_dir=database / "book",
        service_dir=database / "service",
        source="argument",
    )


def make_book_db(
    library: Library,
    book_id: int,
    pages: list[tuple[int, str | None, int | None]],
    titles: list[tuple[int, int, int | None]],
) -> None:
    path = library.book_dir / f"{book_id % 1000:03d}" / f"{book_id}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE page (id INTEGER PRIMARY KEY, part TEXT, page INTEGER, "
        "number INTEGER, services TEXT)"
    )
    connection.execute("CREATE TABLE title (id INTEGER PRIMARY KEY, page INTEGER, parent INTEGER)")
    connection.executemany(
        "INSERT INTO page (id, part, page, number) VALUES (?, ?, ?, NULL)", pages
    )
    connection.executemany("INSERT INTO title (id, page, parent) VALUES (?, ?, ?)", titles)
    connection.commit()
    connection.close()


@pytest.fixture()
def repo(tmp_path: Path) -> BookRepository:
    library = make_library(tmp_path)
    make_book_db(
        library,
        42,
        pages=[
            (1, "1", 1, ),
            (2, "1", 2),
            # Page ids are deliberately sparse: real books have gaps, so neighbours
            # must be looked up rather than computed as id ± 1.
            (7, "1", 3),
            (8, "2", 1),
            (9, "2", 2),
        ],
        titles=[
            (1, 1, None),   # كتاب الطهارة
            (2, 2, 1),      # باب الوضوء
            (3, 7, 2),      # فصل في المسح
            (4, 8, None),   # كتاب الصلاة
        ],
    )
    return BookRepository(library)


class TestPages:
    def test_page_row(self, repo: BookRepository) -> None:
        row = repo.page(42, 7)
        assert row is not None
        assert (row.part, row.printed_page) == ("1", 3)

    def test_unknown_page(self, repo: BookRepository) -> None:
        assert repo.page(42, 999) is None

    def test_unknown_book(self, repo: BookRepository) -> None:
        assert repo.page(777, 1) is None
        assert not repo.exists(777)

    def test_batch_fetch(self, repo: BookRepository) -> None:
        rows = repo.pages(42, [1, 8, 999])
        assert set(rows) == {1, 8}
        assert rows[8].part == "2"

    def test_bounds_and_count(self, repo: BookRepository) -> None:
        assert repo.page_bounds(42) == (1, 9)
        assert repo.page_count(42) == 5

    def test_parts(self, repo: BookRepository) -> None:
        assert repo.parts(42) == ["1", "2"]

    def test_neighbours_respect_gaps(self, repo: BookRepository) -> None:
        before, after = repo.neighbors(42, 7, 1)
        assert before == [2]
        assert after == [8]

    def test_neighbours_at_the_edges(self, repo: BookRepository) -> None:
        before, after = repo.neighbors(42, 1, 2)
        assert before == []
        assert after == [2, 7]

    def test_neighbours_ordering(self, repo: BookRepository) -> None:
        before, _ = repo.neighbors(42, 9, 3)
        assert before == [2, 7, 8]

    def test_resolve_printed_page_with_part(self, repo: BookRepository) -> None:
        assert repo.resolve_printed(42, "2", 1) == 8
        assert repo.resolve_printed(42, "1", 3) == 7

    def test_resolve_printed_page_without_part_takes_the_first(self, repo: BookRepository) -> None:
        assert repo.resolve_printed(42, None, 1) == 1

    def test_resolve_printed_page_that_does_not_exist(self, repo: BookRepository) -> None:
        assert repo.resolve_printed(42, "9", 1) is None


class TestChapterChain:
    def test_full_chain_is_returned_outermost_first(self, repo: BookRepository) -> None:
        assert repo.chapter_title_ids(42, 7) == [1, 2, 3]

    def test_chain_for_a_top_level_heading(self, repo: BookRepository) -> None:
        assert repo.chapter_title_ids(42, 8) == [4]

    def test_page_between_headings_takes_the_preceding_one(self, repo: BookRepository) -> None:
        assert repo.chapter_title_ids(42, 9) == [4]

    def test_page_before_any_heading(self, tmp_path: Path) -> None:
        library = make_library(tmp_path)
        make_book_db(library, 43, pages=[(1, "1", 1), (5, "1", 2)], titles=[(1, 5, None)])
        assert BookRepository(library).chapter_title_ids(43, 1) == []

    def test_book_without_headings(self, tmp_path: Path) -> None:
        library = make_library(tmp_path)
        make_book_db(library, 44, pages=[(1, None, None)], titles=[])
        assert BookRepository(library).chapter_title_ids(44, 1) == []

    def test_cycles_terminate(self, tmp_path: Path) -> None:
        library = make_library(tmp_path)
        # A corrupt parent cycle must not hang the walk.
        make_book_db(
            library, 45, pages=[(1, "1", 1)], titles=[(1, 1, 2), (2, 1, 1)]
        )
        chain = BookRepository(library).chapter_title_ids(45, 1)
        assert len(chain) <= 2
        assert len(set(chain)) == len(chain)

    def test_orphan_parent_is_tolerated(self, tmp_path: Path) -> None:
        library = make_library(tmp_path)
        make_book_db(library, 46, pages=[(1, "1", 1)], titles=[(1, 1, 99)])
        assert BookRepository(library).chapter_title_ids(46, 1) == [1]


class TestTocTree:
    def test_depth_limits_the_tree(self, repo: BookRepository) -> None:
        depth_one = repo.toc_entries(42, max_depth=1)
        assert [row.id for row, _ in depth_one] == [1, 4]

        depth_two = repo.toc_entries(42, max_depth=2)
        assert [row.id for row, _ in depth_two] == [1, 2, 4]

    def test_levels_are_reported(self, repo: BookRepository) -> None:
        entries = repo.toc_entries(42, max_depth=3)
        levels = {row.id: level for row, level in entries}
        assert levels == {1: 1, 2: 2, 3: 3, 4: 1}

    def test_subtree_from_a_title(self, repo: BookRepository) -> None:
        entries = repo.toc_entries(42, from_title=2, max_depth=3)
        assert [row.id for row, _ in entries] == [2, 3]

    def test_unknown_subtree(self, repo: BookRepository) -> None:
        assert repo.toc_entries(42, from_title=99) == []


def test_connections_are_pooled_and_closed(repo: BookRepository) -> None:
    repo.page(42, 1)
    repo.page(42, 2)
    repo.close()
    # Reopening after close must still work.
    assert repo.page(42, 1) is not None
    repo.close()
