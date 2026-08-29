"""Arabic root lookup, for searching every derivation of a word at once.

Shamela indexes a root-stemmed copy of each page in ``m_body`` and caches the
morphological analysis in ``service/S2.db``. Reading that cache lets a search for
"الطلاق" also find "يطلق" and "مطلقة" without re-implementing the morphology.

Two details of that cache matter. It is keyed by the word in its *original* spelling,
so ``بئر`` yields the root ``بءر`` while the folded ``بير`` yields an unrelated set --
lookups must not pre-fold. And a row can exist with no root, meaning the analyser
examined the word and found none; that is different from a word never analysed, but
both leave the caller with no roots to search, so a literal search is used instead.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path

from .normalize import fold, strip_marks

log = logging.getLogger(__name__)

MAX_CACHED_TOKENS = 8192


class RootStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._unavailable = False
        self._cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()

    @property
    def available(self) -> bool:
        return self.path.is_file() and not self._unavailable

    def _connect(self) -> sqlite3.Connection | None:
        if self._unavailable:
            return None
        if self._connection is not None:
            return self._connection
        if not self.path.is_file():
            self._unavailable = True
            return None
        try:
            # Read-only, but not immutable: Shamela appends to this cache as it runs.
            self._connection = sqlite3.connect(
                f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False
            )
        except sqlite3.Error as exc:
            log.debug("root store unavailable: %s", exc)
            self._unavailable = True
            return None
        return self._connection

    def roots(self, word: str) -> tuple[str, ...]:
        """Roots recorded for a word; empty when none are known.

        Pass the word as written (diacritics are fine, they are stripped here). Do not
        pass a folded form: the cache is keyed by original spelling.
        """
        original = strip_marks(word).strip()
        if not original:
            return ()

        with self._lock:
            cached = self._cache.get(original)
            if cached is not None:
                self._cache.move_to_end(original)
                return cached

        result = self._lookup(original)

        with self._lock:
            self._cache[original] = result
            while len(self._cache) > MAX_CACHED_TOKENS:
                self._cache.popitem(last=False)
        return result

    def _lookup(self, original: str) -> tuple[str, ...]:
        connection = self._connect()
        if connection is None:
            return ()

        # Original spelling first; the folded form is a fallback for the rare token
        # that was cached after normalisation.
        candidates = [original]
        folded = fold(original)
        if folded != original:
            candidates.append(folded)

        for candidate in candidates:
            try:
                key = candidate.encode("cp1256")
            except UnicodeEncodeError:
                # Not representable in the encoding Shamela used, so not a valid key.
                continue
            try:
                row = connection.execute(
                    "SELECT root FROM roots WHERE token = ? LIMIT 1", (key,)
                ).fetchone()
            except sqlite3.Error as exc:
                log.debug("root lookup failed for %r: %s", candidate, exc)
                return ()
            if row is None or not row[0]:
                continue

            raw = row[0]
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("cp1256")
                except UnicodeDecodeError:
                    continue

            # Roots are index terms already; keep them byte-exact.
            found: list[str] = []
            for part in str(raw).split(","):
                root = part.strip()
                if root and root not in found:
                    found.append(root)
            if found:
                return tuple(found)
        return ()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
