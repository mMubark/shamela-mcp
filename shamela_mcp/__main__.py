"""Entry point: ``python -m shamela_mcp`` serves; ``--selftest`` verifies an install."""

from __future__ import annotations

import argparse


def selftest() -> int:
    """Exercise the whole stack once and report each step. Used by the installer."""
    from . import __version__
    from .config import load_settings
    from .context import ServerContext

    print(f"shamela-mcp {__version__} — self test")
    failures = 0

    context = ServerContext(load_settings())

    if context.has_library:
        print(f"  [OK]   library: {context.library.root} ({context.library.source})")
    else:
        print("  [FAIL] library: not found")
        for candidate in context.tried or []:
            print(f"         tried {candidate.get('path')}: {candidate.get('problem_ar')}")
        return 1

    try:
        totals = context.catalogue.totals()
        print(
            f"  [OK]   catalogue: {totals['books']} books, "
            f"{totals['downloaded']} downloaded, {totals['categories']} categories"
        )
    except Exception as exc:
        print(f"  [FAIL] catalogue: {exc}")
        failures += 1

    try:
        engine = context.require_engine()
        info = engine.health(force=True)
        print(
            f"  [OK]   engine: java {info.get('java_version')}, "
            f"lucene {info.get('lucene_version')}, "
            f"{info.get('page_docs')} pages indexed"
        )
    except Exception as exc:
        print(f"  [FAIL] engine: {exc}")
        context.shutdown()
        return failures + 1

    try:
        outcome = engine.search(query="الحمد لله", match_mode="phrase", limit=1)
        if outcome.total_hits <= 0 or not outcome.passages:
            print("  [FAIL] search: no results for a phrase that must exist")
            failures += 1
        else:
            passage = outcome.passages[0]
            print(f"  [OK]   search: {outcome.total_hits} hits for «الحمد لله»")
            print(f"  [OK]   citation: {passage.citation.formatted()}")
            if not passage.text:
                print("  [FAIL] page text came back empty")
                failures += 1
    except Exception as exc:
        print(f"  [FAIL] search: {exc}")
        failures += 1

    context.shutdown()
    print("  result:", "PASS" if failures == 0 else f"FAIL ({failures} problem(s))")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="shamela-mcp", add_help=True)
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="verify the library, engine, and a real search, then exit",
    )
    args = parser.parse_args()

    if args.selftest:
        raise SystemExit(selftest())

    from .server import main as serve

    serve()


if __name__ == "__main__":
    main()
