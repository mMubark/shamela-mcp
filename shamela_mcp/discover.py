"""Locating the Shamela installation, its bundled JRE, and its Lucene jars.

A folder is judged by its contents, never by its name: installations get renamed,
copied to external drives, and localised. Every rejected candidate keeps the reason
it failed so shamela_health can say *which* test failed instead of "not found".
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

CANDIDATE_NAMES = (
    "shamela",
    "shamela4",
    "Shamela",
    "Shamela4",
    "Shamela 4",
    "المكتبة الشاملة",
    "المكتبة الشاملة 4",
    "المكتبة الشاملة الحديثة",
)


@dataclass(frozen=True)
class Library:
    root: Path
    database_dir: Path
    app_dir: Path
    master_db: Path
    store_dir: Path
    book_dir: Path
    service_dir: Path
    source: str  # "env" | "argument" | "search"

    @property
    def roots_db(self) -> Path:
        return self.service_dir / "S2.db"


@dataclass(frozen=True)
class Runtime:
    java_path: Path | None
    lucene_dir: Path | None
    problems_ar: tuple[str, ...]


def _database_dir(root: Path) -> Path | None:
    for name in ("database", "Database"):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def check_path(path: str | os.PathLike[str]) -> dict[str, object]:
    """Diagnose a candidate root: what exists, what is missing, and why."""
    root = Path(path)
    result: dict[str, object] = {
        "path": str(root),
        "exists": root.exists(),
        "is_directory": root.is_dir(),
        "has_database": False,
        "has_app": False,
        "has_master_db": False,
        "problem_ar": "",
    }
    if not result["exists"]:
        result["problem_ar"] = "المسار غير موجود."
        return result
    if not result["is_directory"]:
        result["problem_ar"] = "المسار ملف لا مجلد."
        return result

    database = _database_dir(root)
    result["has_database"] = database is not None
    result["has_app"] = (root / "app").is_dir()
    if database is not None:
        master = database / "master.db"
        result["has_master_db"] = master.is_file()
        result["master_db_path"] = str(master)

    if not result["has_database"]:
        result["problem_ar"] = "لا يحوي هذا المجلد مجلد database الخاص بالشاملة."
    elif not result["has_master_db"]:
        result["problem_ar"] = "مجلد database موجود لكن ملف master.db مفقود منه."
    elif not result["has_app"]:
        result["problem_ar"] = (
            "قواعد البيانات موجودة لكن مجلد app مفقود، وفيه جافا وLucene اللذان "
            "يقرآن نصوص الكتب."
        )
    return result


def is_library_root(path: str | os.PathLike[str]) -> bool:
    check = check_path(path)
    return bool(check["has_database"] and check["has_app"] and check["has_master_db"])


def candidate_roots() -> list[Path]:
    """Plausible install locations, in the order worth trying."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        drives = [f"{letter}:\\" for letter in "CDEFGH" if Path(f"{letter}:\\").exists()]
        bases: list[Path] = [Path(d) for d in drives]
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "APPDATA", "USERPROFILE"):
            value = os.environ.get(var)
            if value:
                bases.append(Path(value))
        home = Path.home()
        bases.extend([home, home / "Desktop", home / "Documents", home / "OneDrive" / "Desktop"])
    else:
        home = Path.home()
        bases = [home, home / "Documents", Path("/opt"), Path("/srv")]

    seen: set[str] = set()
    for base in bases:
        for name in CANDIDATE_NAMES:
            candidate = base / name
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
    return candidates


def _build(root: Path, source: str) -> Library:
    database = _database_dir(root)
    assert database is not None  # guarded by is_library_root
    return Library(
        root=root,
        database_dir=database,
        app_dir=root / "app",
        master_db=database / "master.db",
        store_dir=database / "store",
        book_dir=database / "book",
        service_dir=database / "service",
        source=source,
    )


def find_library(explicit: str | None = None) -> tuple[Library | None, list[dict[str, object]]]:
    """Resolve the library root. Returns ``(library, tried)``.

    An explicitly configured path that fails validation is reported rather than
    silently replaced by a scan result -- papering over it would hide the user's
    actual mistake.
    """
    tried: list[dict[str, object]] = []

    for value, source in ((explicit, "argument"), (os.environ.get("SHAMELA_MCP_DIR"), "env")):
        if not value or not value.strip():
            continue
        check = check_path(value.strip().strip('"'))
        check["source"] = source
        tried.append(check)
        if check["has_database"] and check["has_app"] and check["has_master_db"]:
            return _build(Path(str(check["path"])), source), tried
        return None, tried

    for candidate in candidate_roots():
        if not candidate.exists():
            continue
        check = check_path(candidate)
        check["source"] = "search"
        tried.append(check)
        if check["has_database"] and check["has_app"] and check["has_master_db"]:
            return _build(candidate, "search"), tried

    return None, tried


def _newest_dir(parent: Path) -> Path | None:
    if not parent.is_dir():
        return None
    subdirs = [d for d in parent.iterdir() if d.is_dir()]
    if not subdirs:
        return None
    numeric = [d for d in subdirs if d.name.isdigit()]
    pool = numeric or subdirs
    key = (lambda d: int(d.name)) if numeric else (lambda d: d.name)
    return max(pool, key=key)


def find_runtime(library: Library, configured_java: str | None = None) -> Runtime:
    """Locate Shamela's bundled java.exe and its Lucene jar directory."""
    problems: list[str] = []
    java: Path | None = None

    if configured_java:
        candidate = Path(configured_java)
        if candidate.is_file():
            java = candidate
        else:
            problems.append(
                f"مسار جافا المضبوط يدويًا غير موجود ({configured_java})، فتم تجاهله."
            )

    if java is None:
        app = library.app_dir
        exe = "java.exe" if sys.platform == "win32" else "java"
        fixed = [
            app / "win" / "64" / "jre" / "2" / "bin" / exe,
            app / "win" / "32" / "jre" / "2" / "bin" / exe,
            app / "mac" / "64" / "jre" / "2" / "bin" / exe,
            app / "linux" / "64" / "jre" / "2" / "bin" / exe,
        ]
        for candidate in fixed:
            if candidate.is_file():
                java = candidate
                break

    if java is None:
        # Shamela versions its folders (app/win/64/jre/<n>); take the newest.
        for platform_dir in ("win/64", "win/32", "mac/64", "linux/64"):
            jre_parent = library.app_dir.joinpath(*platform_dir.split("/"), "jre")
            newest = _newest_dir(jre_parent)
            if newest is None:
                continue
            exe = "java.exe" if sys.platform == "win32" else "java"
            candidate = newest / "bin" / exe
            if candidate.is_file():
                java = candidate
                break

    if java is None:
        problems.append(
            "لم يُعثر على جافا التي تشحنها الشاملة (المتوقع app\\win\\64\\jre\\2\\bin\\java.exe)."
        )

    lucene = _newest_dir(library.app_dir / "lucene")
    if lucene is None or not any(lucene.glob("*.jar")):
        lucene = None
        problems.append(
            "لم يُعثر على ملفات Lucene التي تشحنها الشاملة (المتوقع app\\lucene\\2)."
        )

    if not (library.store_dir / "page").is_dir():
        problems.append(
            "فهرس الصفحات غير موجود (database\\store\\page). شغّل تطبيق الشاملة مرة "
            "حتى تُبنى الفهارس."
        )

    return Runtime(java_path=java, lucene_dir=lucene, problems_ar=tuple(problems))
