"""Installer: connect this server to Claude Desktop with no configuration.

Written for someone whose only step was double-clicking setup.bat. Every stage prints
an Arabic result, every failure says what to do next, and the whole transcript is kept
in install.log so a problem can be reported without reproducing it.

Design decisions that matter on real machines:

- The Claude config is *merged*, never replaced, and backed up first. Users have other
  MCP servers configured, and losing them would be a serious regression.
- A config file that exists but does not parse is backed up and left alone. Overwriting
  it would discard the user's own work to fix our convenience.
- The registered command is the absolute path to this project's own virtual environment.
  ``"command": "python"`` breaks the moment PATH changes, Python is upgraded, or Anaconda
  shadows it -- the classic silent failure of hand-written MCP configs.
- Nothing needs administrator rights, and nothing is written outside this folder and
  %APPDATA%\\Claude.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn

REPO = Path(__file__).resolve().parent
LOG_PATH = REPO / "install.log"
SERVER_KEY = "shamela"
BACKUPS_KEPT = 5
TOTAL_STEPS = 7

if sys.platform == "win32":
    VENV_PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
else:
    VENV_PYTHON = REPO / ".venv" / "bin" / "python"


# ---------------------------------------------------------------- output


class Log:
    """Console plus a transcript file, both UTF-8."""

    def __init__(self, path: Path) -> None:
        self.handle = None
        try:
            self.handle = path.open("w", encoding="utf-8")
        except OSError:
            pass  # A read-only folder must not stop the install.

    def __call__(self, message: str = "") -> None:
        print(message)
        if self.handle is not None:
            self.handle.write(message + "\n")
            self.handle.flush()

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()


log = Log(LOG_PATH)


def step(number: int, title_ar: str, title_en: str) -> None:
    log("")
    log(f"[{number}/{TOTAL_STEPS}] {title_ar}  |  {title_en}")


def ok(message: str) -> None:
    log(f"   ✓ {message}")


def warn(message: str) -> None:
    log(f"   ! {message}")


def fail(message_ar: str, actions: list[str]) -> NoReturn:
    log("")
    log(f"   ✗ {message_ar}")
    log("")
    log("   ما العمل الآن:")
    for index, action in enumerate(actions, start=1):
        log(f"     {index}) {action}")
    log("")
    log(f"   سجل التثبيت الكامل: {LOG_PATH}")
    log.close()
    raise SystemExit(1)


def ask(prompt_ar: str) -> str:
    log(prompt_ar)
    try:
        return input("   > ").strip()
    except (EOFError, KeyboardInterrupt):
        log("")
        fail(
            "أُلغي التثبيت.",
            ["شغّل setup.bat مرة أخرى حين تكون مستعدًّا."],
        )


# ---------------------------------------------------------------- steps


def check_python() -> None:
    step(1, "التحقّق من إصدار بايثون", "Checking Python")
    if sys.version_info < (3, 10):
        found = ".".join(str(part) for part in sys.version_info[:3])
        fail(
            f"إصدار بايثون المثبَّت ({found}) أقدم من المطلوب (3.10 أو أحدث).",
            [
                "ثبّت بايثون حديثًا من python.org ولا تُغيّر خيارات المثبّت.",
                "ثم شغّل setup.bat مرة أخرى.",
            ],
        )
    ok(f"بايثون {'.'.join(str(p) for p in sys.version_info[:3])} — {sys.executable}")


def claude_config_path() -> Path:
    """Where Claude Desktop keeps its config.

    Read from %APPDATA% rather than assembled from the user profile, so an account
    with a redirected or OneDrive-synced Roaming folder still resolves correctly.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if not base:
            fail(
                "لم يُعرف مجلد إعدادات ويندوز (APPDATA).",
                ["أعد تشغيل الجهاز ثم شغّل setup.bat مرة أخرى."],
            )
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def check_claude(assume_yes: bool) -> Path:
    step(2, "التحقّق من تطبيق Claude Desktop", "Checking Claude Desktop")
    config_path = claude_config_path()
    config_dir = config_path.parent

    if config_dir.is_dir():
        ok(f"مجلد إعدادات كلود موجود: {config_dir}")
        return config_path

    warn("لم يُعثر على مجلد إعدادات Claude Desktop، وقد يعني أن التطبيق غير مثبَّت.")
    if assume_yes:
        config_dir.mkdir(parents=True, exist_ok=True)
        ok("أُنشئ مجلد الإعدادات، وسيقرؤه كلود عند تثبيته.")
        return config_path

    log("")
    log("   نزّل التطبيق من: https://claude.ai/download")
    log("   ثبّته وشغّله مرة واحدة، ثم اضغط Enter هنا للمتابعة.")
    log("   أو اكتب: تخطي — لإكمال التثبيت الآن وترك كلود لوقت لاحق.")
    answer = ask("")
    if answer in ("تخطي", "skip", "s"):
        config_dir.mkdir(parents=True, exist_ok=True)
        ok("أُنشئ مجلد الإعدادات، وسيقرؤه كلود عند تثبيته.")
        return config_path

    if not config_dir.is_dir():
        config_dir.mkdir(parents=True, exist_ok=True)
        warn("ما زال التطبيق غير ظاهر؛ أُنشئ مجلد الإعدادات وسيُقرأ عند تثبيته.")
    else:
        ok(f"مجلد إعدادات كلود موجود: {config_dir}")
    return config_path


def read_configured_library(config_path: Path) -> str | None:
    """Reuse the library path from a previous install, so re-running is idempotent."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    entry = (data.get("mcpServers") or {}).get(SERVER_KEY) or {}
    value = (entry.get("env") or {}).get("SHAMELA_MCP_DIR")
    return value if isinstance(value, str) and value.strip() else None


def locate_library(config_path: Path, explicit: str | None, assume_yes: bool):
    step(3, "البحث عن مجلد المكتبة الشاملة", "Locating the Shamela library")
    sys.path.insert(0, str(REPO))
    from shamela_mcp.discover import check_path, find_library, find_runtime

    for source, value in (
        ("المسار الممرَّر للمثبّت", explicit),
        ("إعدادات كلود السابقة", read_configured_library(config_path)),
        ("متغيّر البيئة SHAMELA_MCP_DIR", os.environ.get("SHAMELA_MCP_DIR")),
    ):
        if not value:
            continue
        library, _ = find_library(value)
        if library is not None:
            ok(f"المكتبة: {library.root}  ({source})")
            return library, find_runtime(library)
        warn(f"{source}: «{value}» لم يصلح — {check_path(value).get('problem_ar')}")

    library, tried = find_library(None)
    if library is not None:
        ok(f"المكتبة: {library.root}  (بحث تلقائي في الأقراص)")
        return library, find_runtime(library)

    log("")
    warn("لم يُعثر على مجلد المكتبة الشاملة تلقائيًّا.")
    if tried:
        log("   المسارات التي جُرّبت:")
        for candidate in tried[:8]:
            log(f"     - {candidate.get('path')}: {candidate.get('problem_ar')}")

    if assume_yes:
        fail(
            "لم يُعثر على المكتبة الشاملة، ولا يمكن السؤال في هذا الوضع.",
            ["شغّل setup.bat بلا معطيات ليطلب منك المسار."],
        )

    log("")
    log("   افتح مجلد المكتبة الشاملة في مستكشف الملفات، وانسخ المسار من شريط")
    log("   العنوان، والصقه هنا ثم اضغط Enter. (مثال: D:\\shamela)")
    for attempt in range(3):
        # Explorer's "Copy as path" wraps the value in quotes.
        answer = ask("").strip().strip('"').rstrip("\\/")
        if not answer:
            warn("لم تُدخل شيئًا.")
            continue
        library, _ = find_library(answer)
        if library is not None:
            ok(f"المكتبة: {library.root}")
            return library, find_runtime(library)
        problem = check_path(answer).get("problem_ar") or "المسار غير صالح."
        warn(problem)
        if attempt < 2:
            log("   جرّب مرة أخرى — والمطلوب المجلد الذي يحوي مجلدي database وapp.")

    fail(
        "لم يُتحقّق من مسار المكتبة الشاملة.",
        [
            "تأكّد أنك تنسخ مسار المجلد الذي يحوي مجلدي database وapp داخله.",
            "إن كانت المكتبة على قرص خارجي فتأكّد أنه موصول.",
            "ثم شغّل setup.bat مرة أخرى.",
        ],
    )


def check_engine_files(library, runtime) -> None:
    step(4, "التحقّق من ملفات محرك البحث", "Checking the search engine files")
    jar = REPO / "java" / "shamela-mcp-helper.jar"
    if jar.is_file():
        ok(f"ملف الوسيط: {jar.name} ({jar.stat().st_size // 1024} ك.ب)")
    else:
        warn(f"ملف الوسيط مفقود: {jar}")
        warn("البحث في النصوص لن يعمل. أعد تنزيل حزمة البرنامج كاملة.")

    if runtime.java_path:
        ok(f"جافا (من ملفات الشاملة): {runtime.java_path}")
    else:
        warn("لم تُوجد جافا التي تشحنها الشاملة؛ البحث في النصوص لن يعمل.")
    if runtime.lucene_dir:
        ok(f"ملفات Lucene: {runtime.lucene_dir}")
    else:
        warn("لم تُوجد ملفات Lucene التي تشحنها الشاملة.")

    for problem in runtime.problems_ar:
        warn(problem)

    if (library.store_dir / "page").is_dir():
        ok("فهرس الصفحات موجود.")
    else:
        warn(
            "فهرس الصفحات غير موجود. شغّل تطبيق المكتبة الشاملة مرة واحدة حتى تُبنى "
            "الفهارس، ثم أعد تشغيل setup.bat."
        )


def build_venv() -> Path:
    step(5, "تهيئة بيئة بايثون وتنزيل المتطلّبات", "Preparing the Python environment")
    venv_dir = REPO / ".venv"

    if not VENV_PYTHON.is_file():
        log("   إنشاء بيئة معزولة داخل مجلد البرنامج…")
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not VENV_PYTHON.is_file():
            log(result.stdout)
            log(result.stderr)
            fail(
                "تعذّر إنشاء بيئة بايثون المعزولة.",
                [
                    "تأكّد أن مضاد الفيروسات لا يمنع الكتابة في مجلد البرنامج.",
                    "انقل مجلد البرنامج إلى مسار أقصر (مثل D:\\shamela-mcp) وأعد المحاولة.",
                ],
            )
        ok("أُنشئت البيئة المعزولة.")
    else:
        ok("البيئة المعزولة موجودة.")

    log("   تنزيل المتطلّبات (يحتاج اتصالًا بالإنترنت مرة واحدة فقط)…")
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        warn("تعذّر تحديث pip؛ سيُكمَل التثبيت.")

    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", str(REPO)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(result.stdout[-2000:])
        log(result.stderr[-2000:])
        fail(
            "تعذّر تنزيل متطلّبات البرنامج.",
            [
                "تأكّد من اتصال الإنترنت (يلزم مرة واحدة عند التثبيت فقط).",
                "إن كنت خلف وكيل (proxy) في شبكة مؤسسة فاطلب من الدعم الفني السماح بـ pypi.org.",
                "ثم شغّل setup.bat مرة أخرى.",
            ],
        )
    ok("نُزّلت المتطلّبات.")
    return VENV_PYTHON


def run_selftest(python: Path, library_root: str) -> bool:
    step(6, "اختبار الاتصال بالمكتبة والبحث فيها", "Testing the library and a real search")
    env = dict(os.environ)
    env["SHAMELA_MCP_DIR"] = library_root
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [str(python), "-X", "utf8", "-m", "shamela_mcp", "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(REPO),
    )
    for line in (result.stdout or "").splitlines():
        log(f"   {line}")
    if result.returncode != 0:
        for line in (result.stderr or "").splitlines()[-10:]:
            log(f"   {line}")
        warn("لم ينجح الاختبار كاملًا.")
        warn(
            "سيُكمَل التثبيت، ويمكنك بعد تشغيل كلود أن تطلب: افحص مكتبة الشاملة — "
            "لعرض سبب الخلل بالتفصيل."
        )
        return False
    ok("الاختبار ناجح: المكتبة تُقرأ والبحث يعمل والعزو يظهر.")
    return True


def prune_backups(config_dir: Path) -> None:
    backups = sorted(
        config_dir.glob("claude_desktop_config.backup-*.json"),
        key=lambda path: path.name,
        reverse=True,
    )
    for stale in backups[BACKUPS_KEPT:]:
        try:
            stale.unlink()
        except OSError:
            pass


def register(config_path: Path, python: Path, library_root: str) -> Path | None:
    step(7, "إضافة الخادم إلى إعدادات كلود", "Registering with Claude Desktop")
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup: Path | None = None

    data: dict = {}
    if config_path.is_file():
        raw = config_path.read_text(encoding="utf-8-sig")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            broken = config_dir / f"claude_desktop_config.backup-broken-{stamp}.json"
            shutil.copy2(config_path, broken)
            fail(
                f"ملف إعدادات كلود موجود لكنه تالف ولا يمكن قراءته ({exc}).",
                [
                    f"نُسخ الملف كما هو إلى: {broken}",
                    "لم يُمسّ الملف الأصلي. أصلحه أو احذفه، ثم شغّل setup.bat مرة أخرى.",
                ],
            )
        if not isinstance(parsed, dict):
            fail(
                "ملف إعدادات كلود ليس على الصورة المتوقّعة.",
                ["احذف الملف أو أصلحه ثم شغّل setup.bat مرة أخرى."],
            )
        data = parsed

        backup = config_dir / f"claude_desktop_config.backup-{stamp}.json"
        shutil.copy2(config_path, backup)
        ok(f"نسخة احتياطية: {backup.name}")
        prune_backups(config_dir)
    else:
        ok("لا ملف إعدادات سابق؛ سيُنشأ ملف جديد.")

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        fail(
            "قسم mcpServers في ملف إعدادات كلود ليس على الصورة المتوقّعة.",
            ["أصلح الملف أو احذفه ثم شغّل setup.bat مرة أخرى."],
        )

    others = [name for name in servers if name != SERVER_KEY]
    updating = SERVER_KEY in servers

    # Only our own entry is touched; every other server stays exactly as it was.
    servers[SERVER_KEY] = {
        "command": str(python),
        "args": ["-m", "shamela_mcp"],
        "env": {"SHAMELA_MCP_DIR": library_root},
    }

    temporary = config_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Atomic replace, so an interrupted write or a syncing folder cannot leave a
    # half-written config behind.
    os.replace(temporary, config_path)

    ok(f"{'حُدِّث' if updating else 'أُضيف'} الخادم «{SERVER_KEY}» في: {config_path}")
    if others:
        ok(f"بقيت الخوادم الأخرى كما هي: {'، '.join(others)}")

    verify = read_configured_library(config_path)
    if verify != library_root:
        fail(
            "كُتب ملف الإعدادات لكن لم تُقرأ منه القيمة المتوقّعة.",
            [
                "إن كنت تستعمل بايثون من متجر مايكروسوفت فأزِله وثبّت بايثون من python.org.",
                f"أو حرّر الملف يدويًّا: {config_path}",
            ],
        )
    ok("تُحقّق من الإعدادات بعد الكتابة.")
    return backup


def finish(library_root: str, config_path: Path, backup: Path | None, healthy: bool) -> None:
    log("")
    log("=" * 66)
    log("   ✅ اكتمل التثبيت")
    log("=" * 66)
    log(f"   المكتبة: {library_root}")
    log(f"   الإعدادات: {config_path}")
    if backup is not None:
        log(f"   النسخة الاحتياطية: {backup.name}")
    if not healthy:
        log("   تنبيه: لم ينجح اختبار البحث؛ انظر التفصيل أعلاه.")
    log("")
    log("   خطوة أخيرة لازمة — أغلق Claude Desktop إغلاقًا تامًّا ثم افتحه:")
    log("     الضغط على ✕ لا يُغلق التطبيق. انقر بالزر الأيمن على أيقونة Claude")
    log("     بجوار الساعة، ثم اختر Quit، ثم افتح التطبيق من جديد.")
    log("")
    log("   ثم اكتب في محادثة جديدة:")
    log("     افحص مكتبة الشاملة")
    log("   وللبحث:")
    log("     ابحث في كتب الفقه الشافعي عن أحكام سجود السهو")
    log("")
    log(f"   سجل التثبيت: {LOG_PATH}")
    log("")


def uninstall() -> int:
    log("إزالة خادم المكتبة الشاملة من إعدادات كلود  |  Uninstalling")
    config_path = claude_config_path()
    if not config_path.is_file():
        log("   لا ملف إعدادات لكلود؛ لا شيء لإزالته.")
        return 0
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log(f"   ✗ تعذّرت قراءة ملف الإعدادات ({exc}); لم يُغيَّر شيء.")
        return 1

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        log("   الخادم غير مسجَّل في إعدادات كلود؛ لا شيء لإزالته.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = config_path.parent / f"claude_desktop_config.backup-{stamp}.json"
    shutil.copy2(config_path, backup)
    log(f"   ✓ نسخة احتياطية: {backup.name}")

    del servers[SERVER_KEY]
    temporary = config_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, config_path)
    log(f"   ✓ أُزيل الخادم «{SERVER_KEY}» من الإعدادات.")
    log("")
    log("   ملفات مكتبتك الشاملة وكتبك لم تُمسّ إطلاقًا.")
    log("   لإزالة البرنامج نهائيًّا: احذف هذا المجلد بعد إغلاق كلود.")
    log("   أغلق Claude Desktop إغلاقًا تامًّا ثم افتحه ليأخذ التغيير مفعوله.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="install.py", description="Install shamela-mcp into Claude Desktop"
    )
    parser.add_argument("--library", help="مسار مجلد المكتبة الشاملة")
    parser.add_argument("--uninstall", action="store_true", help="إزالة الخادم من إعدادات كلود")
    parser.add_argument("--yes", action="store_true", help="لا تسأل أسئلة تفاعلية")
    args = parser.parse_args()

    if args.uninstall:
        return uninstall()

    started = time.time()
    log("=" * 66)
    log("   تثبيت خادم المكتبة الشاملة لـ Claude Desktop")
    log("   Shamela library server for Claude Desktop — installer")
    log("=" * 66)
    log(f"   مجلد البرنامج: {REPO}")

    check_python()
    config_path = check_claude(args.yes)
    library, runtime = locate_library(config_path, args.library, args.yes)
    check_engine_files(library, runtime)
    python = build_venv()
    healthy = run_selftest(python, str(library.root))
    backup = register(config_path, python, str(library.root))
    finish(str(library.root), config_path, backup, healthy)

    log(f"   (استغرق التثبيت {time.time() - started:.0f} ثانية)")
    log.close()
    return 0


if __name__ == "__main__":
    try:
        # Arabic must survive a legacy console codepage.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    raise SystemExit(main())
