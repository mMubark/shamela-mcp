"""Search orchestration: query building, paging, and passage assembly.

This is where a scholar's Arabic phrase becomes Lucene terms, and where a Lucene hit
becomes a citable passage. Page texts are assembled whole -- see ``matchinfo`` for why
nothing is trimmed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import citation as citation_mod
from . import counting, errors, matchinfo
from . import cursor as cursor_mod
from .bookdb import BookRepository
from .bridge import JavaBridge
from .config import MAX_SEARCH_LIMIT
from .discover import Library
from .master import MasterCatalogue
from .normalize import has_arabic, tokenize_pairs
from .roots import RootStore

log = logging.getLogger(__name__)

MATCH_MODES = ("phrase", "all_terms", "any_terms")
SEARCH_MODES = ("exact", "root")

# Shamela's own search panel accepts a handful of words; beyond that a query is
# almost always a pasted sentence, and every extra term narrows the result to nothing.
MAX_QUERY_TOKENS = 10

HEALTH_CACHE_SECONDS = 60.0

# A large book repeats a phrase dozens of times, so ranking by score alone hands the
# whole first batch to one or two books and the scholar never learns that forty other
# books discuss the issue at all. Each book gets a small share of a batch instead, and
# depth inside one book is reached through shamela_search_book.
PER_BOOK_CAP = 2

# Diversifying means looking past the batch: hits are cheap here (ids and scores only,
# no page text), so a wide window is fetched and only the chosen few are assembled.
OVERFETCH_FACTOR = 6
MAX_OVERFETCH = 400


@dataclass
class Passage:
    book_id: int
    page_id: int
    score: float
    citation: citation_mod.Citation
    text: str
    footnote: str
    match_reason_ar: str
    downloaded: bool = True


@dataclass
class SearchOutcome:
    query: str
    match_mode: str
    search_mode: str
    field: str
    groups: list[list[str]]
    total_hits: int
    total_hits_exact: bool
    passages: list[Passage]
    has_more: bool
    next_cursor: str | None
    notes_ar: list[str] = field(default_factory=list)
    books_in_scope: int | None = None
    books_searchable: int | None = None
    scope_label_ar: str = "المكتبة كلها"
    window_start: int = 1
    deferred_same_book: int = 0
    books_in_batch: int = 0


@dataclass
class BookCount:
    """How many pages of one book match, with enough identity to name it."""

    book_id: int
    book_name: str
    author_label: str
    category_name: str
    hits: int

    def name_line(self) -> str:
        return f"{self.book_name} — {self.author_label}"


@dataclass
class CoverageOutcome:
    query: str
    match_mode: str
    search_mode: str
    field: str
    scope_label_ar: str
    books_in_scope: int
    books_with_matches: int
    total_hits: int
    books: list[BookCount]
    shown_hits: int
    truncated: bool
    notes_ar: list[str] = field(default_factory=list)


def select_hits(
    hits: list[dict[str, Any]], limit: int, cap: int
) -> tuple[list[dict[str, Any]], int, int]:
    """Pick a batch from a ranked window, capping how many hits one book may take.

    Returns ``(chosen, deferred, examined)``: the hits to assemble, how many were set
    aside because their book had already filled its share, and how far into the window
    the selection reached. The cursor resumes after exactly ``examined``, so only hits
    that lie *before* the last delivered one are skipped -- and those are counted and
    reported rather than dropped in silence.

    The cap is a preference, not a quota: when the window runs out before the batch is
    full -- which is what happens when only two or three books match at all -- the cap
    is abandoned for this batch and the plain top of the ranking is taken instead.
    Diversity is spent only where there is diversity to spend.
    """
    chosen: list[tuple[int, dict[str, Any]]] = []
    deferred: list[tuple[int, dict[str, Any]]] = []
    per_book: dict[str, int] = {}

    for position, hit in enumerate(hits):
        if len(chosen) >= limit:
            break
        book = str(hit.get("book_id"))
        if cap > 0 and per_book.get(book, 0) >= cap:
            deferred.append((position, hit))
            continue
        per_book[book] = per_book.get(book, 0) + 1
        chosen.append((position, hit))

    if len(chosen) < limit and deferred:
        # The window held fewer books than the cap assumes, so diversifying would cost
        # results without buying variety. Step aside entirely and take the plain top of
        # the ranking: a partial cap here would leave gaps that the cursor then skips.
        head = hits[:limit]
        return head, 0, len(head)

    if not chosen:
        return [], 0, 0

    examined = chosen[-1][0] + 1
    taken = {position for position, _ in chosen}
    skipped = sum(
        1 for position, _ in deferred if position < examined and position not in taken
    )
    return [hit for _, hit in chosen], skipped, examined


class SearchEngine:
    def __init__(
        self,
        library: Library,
        bridge: JavaBridge,
        catalogue: MasterCatalogue,
        books: BookRepository,
        roots: RootStore,
    ) -> None:
        self.library = library
        self.bridge = bridge
        self.catalogue = catalogue
        self.books = books
        self.roots = roots
        self._health: dict[str, Any] = {}
        self._health_at = 0.0
        self._lock = threading.RLock()

    # ---------- index state ----------

    def health(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = time.monotonic() - self._health_at < HEALTH_CACHE_SECONDS
            if self._health and fresh and not force:
                return self._health
            self._health = self.bridge.health()
            self._health_at = time.monotonic()
            return self._health

    def warm_up(self) -> None:
        """Pay the index-open cost in the background, before the first real query."""
        try:
            self.health(force=True)
            self.bridge.search(
                field="body", mode="all_terms", groups=[["الحمد"]], limit=1
            )
            log.debug("engine warm-up complete")
        except Exception as exc:  # pragma: no cover - best effort only
            log.debug("engine warm-up skipped: %s", exc)

    # ---------- query building ----------

    def build_groups(
        self, query: str, search_mode: str
    ) -> tuple[list[list[str]], str, list[str]]:
        """Turn a query into per-position term groups.

        Returns ``(groups, field, notes)``. Root mode expands each word to its roots
        and searches the stemmed field; if any word has no recorded roots the whole
        query falls back to a literal search, because a partially-rooted query would
        silently change what the other words mean.
        """
        text = (query or "").strip()
        if not text:
            raise errors.query_empty()
        if not has_arabic(text):
            raise errors.query_not_arabic(text)

        pairs = tokenize_pairs(text)
        if not pairs:
            raise errors.query_empty()

        notes: list[str] = []
        if len(pairs) > MAX_QUERY_TOKENS:
            notes.append(
                f"اقتُصر البحث على أول {MAX_QUERY_TOKENS} كلمات من الاستعلام؛ "
                "الكلمات الكثيرة تضيّق النتائج حتى تنعدم."
            )
            pairs = pairs[:MAX_QUERY_TOKENS]

        literal = [[folded] for folded, _ in pairs]
        if search_mode != "root":
            return literal, "body", notes

        if not self.roots.available:
            notes.append(
                "قاعدة الجذور (S2.db) غير متاحة في هذه المكتبة، فتم البحث النصّي الحرفي."
            )
            return literal, "body", notes

        groups: list[list[str]] = []
        for _, original in pairs:
            # Look up the word as written: the morphology cache is keyed by original
            # spelling, and a folded key returns roots for a different word.
            roots = self.roots.roots(original)
            if not roots:
                notes.append(
                    f"لا جذر مسجَّلًا للكلمة «{original}» في قاعدة الشاملة، "
                    "فتم البحث النصّي الحرفي بدل البحث بالجذر."
                )
                return literal, "body", notes
            groups.append(list(roots))

        notes.append(
            "البحث بالجذر يشمل مشتقات الكلمة (مثل: الطلاق ← يطلق، مطلقة، طالق)، "
            "وقد يوسّع النتائج توسيعًا كبيرًا."
        )
        return groups, "m_body", notes

    # ---------- search ----------

    def search(
        self,
        *,
        query: str,
        match_mode: str = "all_terms",
        search_mode: str = "exact",
        book_ids: list[int] | None = None,
        limit: int = 5,
        cursor_token: str | None = None,
        scope_label_ar: str = "المكتبة كلها",
        books_in_scope: int | None = None,
    ) -> SearchOutcome:
        if match_mode not in MATCH_MODES:
            raise errors.bad_argument(
                f"نمط المطابقة «{match_mode}» غير معروف.",
                "استعمل phrase أو all_terms أو any_terms.",
                f"invalid match_mode {match_mode!r}",
            )
        if search_mode not in SEARCH_MODES:
            raise errors.bad_argument(
                f"نمط البحث «{search_mode}» غير معروف.",
                "استعمل exact للبحث الحرفي أو root للبحث بالجذر.",
                f"invalid search_mode {search_mode!r}",
            )

        limit = max(1, min(int(limit or 1), MAX_SEARCH_LIMIT))
        groups, field, notes = self.build_groups(query, search_mode)
        scope = [str(b) for b in (book_ids or [])]

        health = self.health()
        fingerprint = cursor_mod.index_fingerprint(health)
        query_key = cursor_mod.query_hash(
            field=field, mode=match_mode, groups=groups, book_ids=scope or None
        )

        after_doc: int | None = None
        after_score: float | None = None
        delivered = 0
        known_total: int | None = None
        if cursor_token:
            resumed = cursor_mod.decode(
                cursor_token, fingerprint=fingerprint, expected_query_hash=query_key
            )
            after_doc = resumed.after_doc
            after_score = resumed.after_score
            delivered = resumed.delivered
            known_total = resumed.total

        # One book needs no diversifying, and asking Lucene for six times the pages
        # there would only slow an already narrow search.
        diversify = len(scope) != 1
        cap = PER_BOOK_CAP if diversify else 0
        fetch = min(limit * OVERFETCH_FACTOR, MAX_OVERFETCH) if diversify else limit

        raw = self.bridge.search(
            field=field,
            mode=match_mode,
            groups=groups,
            book_ids=scope or None,
            limit=fetch,
            after_doc=after_doc,
            after_score=after_score,
        )

        window = raw.get("hits", [])
        hits, deferred, examined = select_hits(window, limit, cap)
        total = known_total if known_total is not None else int(raw.get("total_hits", 0))
        passages = self.assemble(hits, groups=groups, match_mode=match_mode, query=query,
                                root_mode=(field == "m_body"))

        # There is more either because the fetched window was not read to its end, or
        # because Lucene had ranked hits beyond it.
        has_more = bool(window) and (examined < len(window) or bool(raw.get("has_more")))
        next_cursor: str | None = None
        if has_more and examined:
            edge = window[examined - 1]
            try:
                after = int(edge["doc"])
            except (KeyError, TypeError, ValueError):
                after = int(raw.get("last_doc", -1))
            if after >= 0:
                next_cursor = cursor_mod.encode(
                    cursor_mod.Cursor(
                        fingerprint=fingerprint,
                        query_hash=query_key,
                        after_doc=after,
                        after_score=float(edge.get("score", 0.0)),
                        delivered=delivered + len(passages),
                        total=total,
                    )
                )

        if deferred:
            notes.append(
                f"أُخِّر {counting.places(deferred)} من كتبٍ استوفت حصّتها في هذه الدفعة "
                f"({PER_BOOK_CAP} لكل كتاب) لتنويع الكتب المعروضة، وهي مواضع لا تعود في "
                "الدفعات التالية. لاستيفاء مواضع كتاب بعينه استعمل shamela_search_book، "
                "ولمعرفة توزيع المطابقات على الكتب استعمل shamela_search_coverage."
            )

        if not raw.get("total_hits_exact", True):
            notes.append(
                "تعذّر حصر النطاق داخل الفهرس، فعدد المطابقات الإجمالي تقريبي "
                "(النتائج المعروضة صحيحة ومحصورة في النطاق)."
            )

        return SearchOutcome(
            query=query,
            match_mode=match_mode,
            search_mode="root" if field == "m_body" else "exact",
            field=field,
            groups=groups,
            total_hits=total,
            total_hits_exact=bool(raw.get("total_hits_exact", True)),
            passages=passages,
            has_more=has_more,
            next_cursor=next_cursor,
            notes_ar=notes,
            books_in_scope=books_in_scope,
            scope_label_ar=scope_label_ar,
            window_start=delivered + 1,
            deferred_same_book=deferred,
            books_in_batch=len({p.book_id for p in passages}),
        )

    # ---------- coverage ----------

    def coverage(
        self,
        *,
        query: str,
        match_mode: str = "all_terms",
        search_mode: str = "exact",
        book_ids: list[int],
        top: int = 25,
        scope_label_ar: str = "المكتبة كلها",
    ) -> CoverageOutcome:
        """Count the matches book by book across the whole scope.

        Ordinary search answers *what* matched best; this answers *where* the matches
        are -- how many of the books in scope hold the phrase at all, and how the hits
        divide among them. It is what turns "I searched 153 books" from a claim about
        the scope into a statement about the result, and it reads only counts, so a
        whole-library sweep costs one round trip rather than thousands of pages.
        """
        if match_mode not in MATCH_MODES:
            raise errors.bad_argument(
                f"نمط المطابقة «{match_mode}» غير معروف.",
                "استعمل phrase أو all_terms أو any_terms.",
                f"invalid match_mode {match_mode!r}",
            )
        if search_mode not in SEARCH_MODES:
            raise errors.bad_argument(
                f"نمط البحث «{search_mode}» غير معروف.",
                "استعمل exact للبحث الحرفي أو root للبحث بالجذر.",
                f"invalid search_mode {search_mode!r}",
            )

        groups, field, notes = self.build_groups(query, search_mode)
        scope = [str(b) for b in book_ids]
        if not scope:
            raise errors.bad_argument(
                "لا كتب منزَّلة في النطاق المطلوب.",
                "اختر قسمًا فيه كتب منزَّلة، أو نزّل كتب القسم من تطبيق الشاملة.",
                "empty coverage scope",
            )

        raw = self.bridge.count_by_book(
            field=field, mode=match_mode, groups=groups, book_ids=scope
        )
        if not raw.get("supported", True):
            raise errors.engine_unavailable(
                "count_by_book unsupported: no book field in index",
                {"problems_ar": ["فهرس هذه المكتبة لا يحمل حقل الكتاب، فتعذّر عدّ المطابقات لكل كتاب."]},
            )

        rows = raw.get("counts", [])
        total = 0
        books: list[BookCount] = []
        limit = max(1, int(top or 1))
        for row in rows:
            try:
                book_id = int(row["book_id"])
                hits = int(row["hits"])
            except (KeyError, TypeError, ValueError):
                continue
            total += hits
            if len(books) >= limit:
                continue
            book = self.catalogue.book(book_id)
            books.append(
                BookCount(
                    book_id=book_id,
                    book_name=book.name if book else f"كتاب غير معروف ({book_id})",
                    author_label=book.author_label if book else "المؤلف غير مذكور",
                    category_name=book.category_name if book else "قسم غير معروف",
                    hits=hits,
                )
            )

        return CoverageOutcome(
            query=query,
            match_mode=match_mode,
            search_mode="root" if field == "m_body" else "exact",
            field=field,
            scope_label_ar=scope_label_ar,
            books_in_scope=len(scope),
            books_with_matches=len(rows),
            total_hits=total,
            books=books,
            shown_hits=sum(b.hits for b in books),
            truncated=len(rows) > len(books),
            notes_ar=notes,
        )

    # ---------- passage assembly ----------

    def assemble(
        self,
        hits: list[dict[str, Any]],
        *,
        groups: list[list[str]],
        match_mode: str,
        query: str,
        root_mode: bool,
    ) -> list[Passage]:
        """Turn Lucene hits into cited passages, in as few round trips as possible."""
        if not hits:
            return []

        wanted: dict[int, list[int]] = {}
        order: list[tuple[int, int, float]] = []
        for hit in hits:
            try:
                book_id = int(hit["book_id"])
                page_id = int(hit["page_id"])
            except (KeyError, TypeError, ValueError):
                continue
            wanted.setdefault(book_id, []).append(page_id)
            order.append((book_id, page_id, float(hit.get("score", 0.0))))

        texts = self.bridge.get_pages([(b, ids) for b, ids in wanted.items()])
        chapters = self._chapter_texts(
            {book_id: page_ids for book_id, page_ids in wanted.items()}
        )

        passages: list[Passage] = []
        for book_id, page_id, score in order:
            book = self.catalogue.book(book_id)
            if book is None:
                continue
            row = texts.get(book_id, {}).get(page_id, {})
            body = citation_mod.html_to_text(row.get("body"))
            footnote = citation_mod.html_to_text(row.get("foot"))

            page_row = self.books.page(book_id, page_id)
            evidence = matchinfo.evaluate(
                body=body,
                footnote=footnote,
                groups=groups,
                match_mode=match_mode,
                query=query,
                root_mode=root_mode,
            )
            passages.append(
                Passage(
                    book_id=book_id,
                    page_id=page_id,
                    score=score,
                    citation=citation_mod.build(
                        book,
                        page_id,
                        part=page_row.part if page_row else None,
                        printed_page=page_row.printed_page if page_row else None,
                        chapter_path=chapters.get((book_id, page_id), []),
                    ),
                    text=body,
                    footnote=footnote,
                    match_reason_ar=evidence.reason_ar,
                    downloaded=book.downloaded,
                )
            )
        return passages

    def _chapter_texts(self, wanted: dict[int, list[int]]) -> dict[tuple[int, int], list[str]]:
        """Resolve full heading chains for a batch of pages in one Lucene call."""
        chains: dict[tuple[int, int], list[int]] = {}
        needed: dict[int, set[int]] = {}
        for book_id, page_ids in wanted.items():
            for page_id in page_ids:
                chain = self.books.chapter_title_ids(book_id, page_id)
                if chain:
                    chains[(book_id, page_id)] = chain
                    needed.setdefault(book_id, set()).update(chain)

        if not needed:
            return {}

        titles = self.bridge.get_titles([(b, sorted(ids)) for b, ids in needed.items()])

        out: dict[tuple[int, int], list[str]] = {}
        for key, chain in chains.items():
            book_id = key[0]
            rows = titles.get(book_id, {})
            path: list[str] = []
            for title_id in chain:
                text = citation_mod.heading_text(rows.get(title_id, {}).get("body"))
                if text:
                    path.append(text)
            if path:
                out[key] = path
        return out

    def page_with_context(
        self, book_id: int, page_id: int, neighbors: int = 0
    ) -> list[Passage]:
        """A page plus surrounding pages, each fully cited."""
        book = self.catalogue.book(book_id)
        if book is None:
            raise errors.book_not_found(book_id)
        if not self.books.exists(book_id):
            raise errors.book_not_downloaded(book_id, book.name)

        before, after = self.books.neighbors(book_id, page_id, neighbors)
        page_ids = [*before, page_id, *after]

        texts = self.bridge.get_pages([(book_id, page_ids)])
        rows = texts.get(book_id, {})
        if not rows.get(page_id, {}).get("found"):
            bounds = self.books.page_bounds(book_id)
            available = f"{bounds[0]}–{bounds[1]}" if bounds else None
            raise errors.page_not_found(book_id, page_id, available)

        chapters = self._chapter_texts({book_id: page_ids})
        page_rows = self.books.pages(book_id, page_ids)

        passages: list[Passage] = []
        for current in page_ids:
            row = rows.get(current, {})
            if not row.get("found"):
                continue
            page_row = page_rows.get(current)
            label = (
                "الصفحة المطلوبة"
                if current == page_id
                else ("صفحة سابقة للسياق" if current < page_id else "صفحة تالية للسياق")
            )
            passages.append(
                Passage(
                    book_id=book_id,
                    page_id=current,
                    score=0.0,
                    citation=citation_mod.build(
                        book,
                        current,
                        part=page_row.part if page_row else None,
                        printed_page=page_row.printed_page if page_row else None,
                        chapter_path=chapters.get((book_id, current), []),
                    ),
                    text=citation_mod.html_to_text(row.get("body")),
                    footnote=citation_mod.html_to_text(row.get("foot")),
                    match_reason_ar=label,
                    downloaded=True,
                )
            )
        return passages
