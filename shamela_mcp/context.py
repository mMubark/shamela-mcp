"""Process-wide state shared by every tool.

The library is discovered once, the catalogue is loaded once, and the Java helper is
started once. Tools reach everything through this object so they never own lifecycle.

A missing library is not fatal here: the catalogue tools still work off SQLite, and
shamela_health exists precisely to explain what is wrong. Only the tools that need
page text refuse to run.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from . import errors
from .bookdb import BookRepository
from .bridge import JavaBridge
from .config import Settings, load_settings
from .discover import Library, Runtime, find_library, find_runtime
from .engine import SearchEngine
from .master import MasterCatalogue
from .roots import RootStore

log = logging.getLogger(__name__)


class ServerContext:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._lock = threading.RLock()

        self.library: Library | None
        self.tried: list[dict[str, Any]]
        self.library, self.tried = find_library(self.settings.library_dir)

        self.runtime: Runtime | None = (
            find_runtime(self.library, self.settings.java_path) if self.library else None
        )

        self._catalogue: MasterCatalogue | None = None
        self._books: BookRepository | None = None
        self._roots: RootStore | None = None
        self._bridge: JavaBridge | None = None
        self._engine: SearchEngine | None = None

    # ---------- availability ----------

    @property
    def has_library(self) -> bool:
        return self.library is not None

    def require_library(self) -> Library:
        if self.library is None:
            raise errors.library_not_found(self.tried)
        return self.library

    def require_engine(self) -> SearchEngine:
        """The search engine, or an Arabic explanation of why there is none."""
        self.require_library()
        with self._lock:
            if self._engine is None:
                if self.runtime is None:
                    raise errors.engine_unavailable("runtime not resolved")
                if self.runtime.java_path is None or self.runtime.lucene_dir is None:
                    raise errors.engine_unavailable(
                        "java or lucene not found",
                        {"problems_ar": list(self.runtime.problems_ar)},
                    )
                self._engine = SearchEngine(
                    self.require_library(),
                    self.bridge,
                    self.catalogue,
                    self.books,
                    self.roots,
                )
            return self._engine

    # ---------- lazy components ----------

    @property
    def catalogue(self) -> MasterCatalogue:
        with self._lock:
            if self._catalogue is None:
                self._catalogue = MasterCatalogue(self.require_library())
            return self._catalogue

    @property
    def books(self) -> BookRepository:
        with self._lock:
            if self._books is None:
                self._books = BookRepository(self.require_library())
            return self._books

    @property
    def roots(self) -> RootStore:
        with self._lock:
            if self._roots is None:
                self._roots = RootStore(self.require_library().roots_db)
            return self._roots

    @property
    def bridge(self) -> JavaBridge:
        with self._lock:
            if self._bridge is None:
                if self.runtime is None:
                    raise errors.engine_unavailable("runtime not resolved")
                self._bridge = JavaBridge(
                    self.require_library(),
                    self.runtime,
                    timeout_ms=self.settings.timeout_ms,
                    idle_ms=self.settings.idle_ms,
                )
            return self._bridge

    # ---------- book resolution ----------

    def require_book(self, book_id: int):
        book = self.catalogue.book(book_id)
        if book is None:
            raise errors.book_not_found(book_id)
        return book

    def require_downloaded_book(self, book_id: int):
        book = self.require_book(book_id)
        if not self.books.exists(book_id):
            raise errors.book_not_downloaded(book_id, book.name)
        return book

    def resolve_categories(self, given: list[str]) -> list:
        """Resolve category numbers or Arabic names, reporting the first failure."""
        resolved = []
        seen: set[int] = set()
        for item in given:
            category = self.catalogue.resolve_category(str(item))
            if category is None:
                raise errors.category_unknown(
                    str(item),
                    [
                        {"id": c.id, "name": c.name}
                        for c in self.catalogue.closest_categories(str(item))
                    ],
                )
            if category.id not in seen:
                seen.add(category.id)
                resolved.append(category)
        return resolved

    # ---------- lifecycle ----------

    def warm_up(self) -> None:
        """Open the index in the background so the first real query is not the slow one."""
        def run() -> None:
            try:
                self.require_engine().warm_up()
            except Exception as exc:  # pragma: no cover - best effort
                log.debug("warm-up unavailable: %s", exc)

        if not self.has_library:
            return
        threading.Thread(target=run, name="warm-up", daemon=True).start()

    def shutdown(self) -> None:
        with self._lock:
            for component in (self._bridge, self._books, self._roots, self._catalogue):
                if component is None:
                    continue
                try:
                    component.close()
                except Exception:  # pragma: no cover - shutdown best effort
                    pass
            self._bridge = None
            self._engine = None
