"""Check the folding rules against a real index.

Search works by folding the query the same way Shamela folded the text when it built
its index. If one rule differs, every query containing that letter returns nothing --
and nothing looks broken. This script asserts each rule directly: the folded form must
be present in the index and the unfolded form must be absent.

Run it after changing anything in ``normalize.py``:

    python scripts/probe_index.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shamela_mcp.config import load_settings  # noqa: E402
from shamela_mcp.context import ServerContext  # noqa: E402
from shamela_mcp.normalize import tokenize  # noqa: E402


def indexed_term(word: str) -> str:
    """The term a query for ``word`` actually looks for.

    Not simply ``fold``: some rules apply per token (ابن is indexed as بن), and it is
    the tokenised form that reaches Lucene.
    """
    terms = tokenize(word)
    return terms[0] if terms else word


# Words chosen so that folding changes them, with the unfolded spelling being the one
# a user would naturally type.
CASES = [
    ("hamza on ya    (ئ -> ي)", "بئر"),
    ("hamza on ya    (ئ -> ي)", "المسائل"),
    ("ta marbuta     (ة -> ه)", "مكة"),
    ("ta marbuta     (ة -> ه)", "صلاة"),
    ("alef family    (أ -> ا)", "الأمر"),
    ("alef family    (إ -> ا)", "إسلام"),
    ("alef family    (آ -> ا)", "القرآن"),
    ("alef maqsura   (ى -> ي)", "على"),
    ("hamza on waw   (ؤ -> و)", "مؤمن"),
    ("ibn token      (ابن -> بن)", "ابن"),
]

EXTRA_FIELDS = [
    ("m_body", "root field", ["طلق", "صلو", "ءبو"]),
    ("n_body", "number field", ["1", "256"]),
]


def main() -> int:
    context = ServerContext(load_settings())
    if not context.has_library:
        print("No Shamela library found. Set SHAMELA_MCP_DIR or run setup.bat.")
        for candidate in context.tried or []:
            print(f"  tried {candidate.get('path')}: {candidate.get('problem_ar')}")
        return 1

    print(f"library: {context.library.root}")

    try:
        info = context.require_engine().health(force=True)
    except Exception as exc:
        print(f"engine unavailable: {exc}")
        context.shutdown()
        return 1

    print(f"index:   {info.get('page_docs'):,} pages, generation {info.get('page_generation')}")
    print(f"lucene:  {info.get('lucene_version')} on java {info.get('java_version')}")
    print(f"scope field: {info.get('book_field')}")
    print()

    terms: list[str] = []
    for _, original in CASES:
        terms.extend([indexed_term(original), original])
    terms = list(dict.fromkeys(terms))

    result = context.bridge.probe(index="page", field="body", terms=terms)
    frequencies = result.get("docfreqs", {})

    print("body field — each folded form must be indexed, each raw form must not be:")
    print(f"  {'rule':<28} {'folded':<12} {'docs':>12}   {'raw':<12} {'docs':>10}  verdict")
    failures = 0
    for rule, original in CASES:
        folded = indexed_term(original)
        folded_docs = int(frequencies.get(folded, 0) or 0)
        raw_docs = int(frequencies.get(original, 0) or 0)
        passed = folded_docs > 0 and raw_docs == 0
        failures += 0 if passed else 1
        print(
            f"  {rule:<28} {folded:<12} {folded_docs:>12,}   "
            f"{original:<12} {raw_docs:>10,}  {'ok' if passed else 'MISMATCH'}"
        )

    print()
    for field, label, sample in EXTRA_FIELDS:
        try:
            extra = context.bridge.probe(index="page", field=field, terms=sample)
        except Exception as exc:
            print(f"{field} ({label}): unavailable — {exc}")
            continue
        counts = extra.get("docfreqs", {})
        rendered = ", ".join(f"{term}={counts.get(term, 0):,}" for term in sample)
        live = any(int(counts.get(term, 0) or 0) > 0 for term in sample)
        print(f"{field} ({label}): {rendered}  {'ok' if live else 'EMPTY'}")
        if not live:
            failures += 1

    print()
    print("indexed fields:", ", ".join(result.get("fields", [])))

    if context.roots.available:
        for word in ("الطلاق", "بئر", "صلاة"):
            print(f"roots({word}) = {context.roots.roots(word) or '(none recorded)'}")
    else:
        print("root store (S2.db): not available")

    context.shutdown()

    print()
    if failures:
        print(f"FAIL: {failures} check(s) disagree with the index.")
        print("A mismatch means queries containing that letter can return nothing.")
        return 1
    print("PASS: folding rules match the index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
