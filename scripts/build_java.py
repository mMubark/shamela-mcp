"""Build the Lucene helper jar. Developer-only: users get the committed jar.

Compilation needs a real JDK 21 and the Lucene API. The JRE Shamela ships has no
compiler, and its jars are not ours to redistribute, so lucene-core is fetched from
Maven Central purely to compile against -- nothing from it is packaged. At runtime the
helper loads Shamela's own jars from the user's installation.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JAVA_SRC = REPO / "java" / "src"
CLASSES = REPO / "java" / "classes"
JAR = REPO / "java" / "shamela-mcp-helper.jar"
BUILD_DIR = REPO / ".lucene-build"

LUCENE_VERSION = "10.4.0"
MAVEN_BASE = (
    "https://repo1.maven.org/maven2/org/apache/lucene/lucene-core/"
    f"{LUCENE_VERSION}/lucene-core-{LUCENE_VERSION}.jar"
)
REQUIRED_JAVA = 21


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def javac_version(javac: str) -> tuple[int, str] | None:
    """Return ``(major, raw)`` for a javac executable, or None if unusable."""
    try:
        # javac historically printed its version to stderr; read both streams.
        proc = subprocess.run([javac, "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = f"{proc.stdout} {proc.stderr}".strip()
    match = re.search(r"(\d+)", raw)
    if match is None:
        return None
    return int(match.group(1)), raw


def candidate_javacs() -> list[str]:
    """javac executables worth trying, best-known locations first.

    PATH order is not a reliable guide: a machine can have JDK 21 installed while an
    older JDK sits earlier on PATH, so the well-known install roots are scanned too.
    """
    candidates: list[str] = []

    from_env = os.environ.get("JAVA_HOME")
    if from_env:
        exe = "javac.exe" if sys.platform == "win32" else "javac"
        candidates.append(str(Path(from_env) / "bin" / exe))

    on_path = shutil.which("javac")
    if on_path:
        candidates.append(on_path)

    if sys.platform == "win32":
        roots = [
            Path(r"C:\Program Files\Eclipse Adoptium"),
            Path(r"C:\Program Files\Java"),
            Path(r"C:\Program Files\Microsoft\jdk"),
            Path(r"C:\Program Files\Amazon Corretto"),
            Path(r"C:\Program Files\Zulu"),
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir(), reverse=True):
                exe = entry / "bin" / "javac.exe"
                if exe.is_file():
                    candidates.append(str(exe))
    else:
        for root in (Path("/usr/lib/jvm"), Path("/Library/Java/JavaVirtualMachines")):
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir(), reverse=True):
                for exe in (entry / "bin" / "javac", entry / "Contents" / "Home" / "bin" / "javac"):
                    if exe.is_file():
                        candidates.append(str(exe))

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def check_javac() -> str:
    found: list[str] = []
    for candidate in candidate_javacs():
        version = javac_version(candidate)
        if version is None:
            continue
        major, raw = version
        found.append(f"{candidate} ({raw})")
        if major >= REQUIRED_JAVA:
            print(f"[1/4] javac OK: {candidate} ({raw})")
            return candidate

    if found:
        listing = "\n  ".join(found)
        fail(
            f"no javac {REQUIRED_JAVA}+ found; Lucene {LUCENE_VERSION} requires it.\n"
            f"  Tried:\n  {listing}\n"
            f"  Install a JDK {REQUIRED_JAVA}+ or point JAVA_HOME at one."
        )
    fail(
        f"javac not found. Install a JDK {REQUIRED_JAVA} or newer (Temurin, Oracle, or "
        "Microsoft OpenJDK) and set JAVA_HOME. Shamela's bundled runtime is a JRE and "
        "cannot compile."
    )
    raise AssertionError("unreachable")


def fetch_lucene() -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    jar = BUILD_DIR / f"lucene-core-{LUCENE_VERSION}.jar"
    if jar.is_file():
        print(f"[2/4] lucene-core present ({jar.name})")
        return jar

    print(f"[2/4] downloading {MAVEN_BASE}")
    with urllib.request.urlopen(MAVEN_BASE, timeout=120) as response:
        payload = response.read()
    with urllib.request.urlopen(MAVEN_BASE + ".sha1", timeout=60) as response:
        expected = response.read().decode("ascii").split()[0].strip().lower()

    actual = hashlib.sha1(payload).hexdigest()
    if actual != expected:
        fail(f"sha1 mismatch for lucene-core: expected {expected}, got {actual}")

    jar.write_bytes(payload)
    print(f"      verified sha1 {actual}")
    return jar


def compile_sources(javac: str, lucene_jar: Path) -> None:
    if CLASSES.exists():
        shutil.rmtree(CLASSES)
    CLASSES.mkdir(parents=True)

    sources = sorted(str(p) for p in JAVA_SRC.rglob("*.java"))
    if not sources:
        fail(f"no Java sources under {JAVA_SRC}")

    cmd = [
        javac,
        "--release", str(REQUIRED_JAVA),
        "-encoding", "UTF-8",
        "-Xlint:-options",
        "-classpath", str(lucene_jar),
        "-d", str(CLASSES),
        *sources,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        fail("compilation failed")
    print(f"[3/4] compiled {len(sources)} source files")


def package(javac: str) -> None:
    # Use the jar from the same JDK as javac, not whichever one PATH happens to offer.
    sibling = Path(javac).with_name("jar.exe" if sys.platform == "win32" else "jar")
    jar_tool = str(sibling) if sibling.is_file() else shutil.which("jar")
    if jar_tool is None:
        fail("jar tool not found; it ships with the JDK alongside javac")
    if JAR.exists():
        JAR.unlink()
    cmd = [jar_tool, "--create", "--file", str(JAR), "-C", str(CLASSES), "."]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        fail("jar packaging failed")

    import zipfile

    with zipfile.ZipFile(JAR) as archive:
        names = set(archive.namelist())
    if "dev/shamela/mcp/Main.class" not in names:
        fail("built jar is missing dev/shamela/mcp/Main.class")

    size_kb = JAR.stat().st_size / 1024
    print(f"[4/4] wrote {JAR.relative_to(REPO)} ({size_kb:.1f} KB, {len(names)} entries)")


def main() -> None:
    javac = check_javac()
    lucene_jar = fetch_lucene()
    compile_sources(javac, lucene_jar)
    package(javac)
    print("\nHelper jar built. Commit it: users have no JDK.")


if __name__ == "__main__":
    main()
