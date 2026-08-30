"""Diagnosis.

This is the one tool that treats a broken library as a successful answer: when nothing
works, the useful reply is a precise account of what is missing, not an error.

The folding probes are the part worth understanding. Search works by folding the query
the same way Shamela folded the text when it built its index; if a single rule differs,
queries containing that letter return nothing at all, silently. Each probe asserts a
rule against the live index -- ``بير`` must be found and ``بئر`` must not, because
Shamela stores the folded form -- so a mismatch surfaces as a named finding instead of
mysteriously empty results.
"""

from __future__ import annotations

import time
from typing import Any

from mcp.types import CallToolResult

from .. import RUNNING_BUILD, __version__, build_id, errors
from ..bridge import HELPER_JAR
from ..config import NORMALIZER_VERSION
from .shared import reply, tool

# (rule, term expected present, term expected absent)
FOLDING_PROBES = (
    ("همزة على نبرة تُطبَّع ياءً (ئ ← ي)", "بير", "بئر"),
    ("تاء مربوطة تُطبَّع هاءً (ة ← ه)", "مكه", "مكة"),
    ("همزات الألف تُطبَّع ألفًا (أ إ آ ← ا)", "الامر", "الأمر"),
    ("ألف مقصورة تُطبَّع ياءً (ى ← ي)", "علي", "على"),
    ("«ابن» تُفهرس «بن»", "بن", "ابن"),
)


def register(mcp, context) -> None:

    @mcp.tool(
        name="shamela_health",
        description=(
            "أداة تشخيصية: تفحص مسار المكتبة الشاملة، وقواعد بياناتها، وفهرس البحث، "
            "وجافا التي تشغّل البحث، وقاعدة الجذور، وتطابق قواعد التطبيع مع الفهرس، "
            "وتعرض عدد الكتب والمنزَّل منها. تجيب طلب المستخدم «افحص مكتبة الشاملة». "
            "ابدأ بها عند أي سلوك غير متوقّع أو نتائج فارغة بلا سبب ظاهر."
        ),
    )
    @tool("shamela_health")
    def shamela_health() -> CallToolResult:
        lines: list[str] = [f"فحص خادم المكتبة الشاملة — الإصدار {__version__}", ""]
        warnings: list[str] = []
        structured: dict[str, Any] = {
            "version": __version__,
            "normalizer_version": NORMALIZER_VERSION,
            "environment": context.settings.env_report,
        }

        # ---- is this process running the code currently on disk? ----
        # Claude Desktop keeps the server process alive across chats, so a fix applied
        # on disk stays dormant until the app is fully quit. Without this check that
        # gap looks exactly like a fix that did not work.
        on_disk = build_id()
        structured["build"] = RUNNING_BUILD
        structured["build_on_disk"] = on_disk
        structured["build_is_current"] = RUNNING_BUILD == on_disk
        if RUNNING_BUILD != on_disk:
            lines.append(
                "✗ النسخة العاملة أقدم من ملفات الخادم على القرص "
                f"(العاملة {RUNNING_BUILD}، وعلى القرص {on_disk})."
            )
            lines.append(
                "   أغلق Claude Desktop إغلاقًا تامًّا — بالزر الأيمن على أيقونته بجوار "
                "الساعة ثم Quit، فالضغط على ✕ لا يُغلقه — ثم افتحه لتسري التحديثات."
            )
            lines.append("")
            warnings.append("الخادم يعمل بنسخة قديمة؛ يلزم إغلاق كلود وفتحه")

        # ---- library ----
        if not context.has_library:
            lines.append("✗ المكتبة: لم يُعثر على مجلد المكتبة الشاملة.")
            lines.append("")
            lines.append("المسارات التي جُرّبت:")
            for candidate in context.tried or []:
                problem = candidate.get("problem_ar") or "غير صالح"
                lines.append(f"- {candidate.get('path')} — {problem}")
            lines.append("")
            lines.append(
                "ما العمل: شغّل setup.bat مرة أخرى ليطلب منك مسار المكتبة، أو تأكد أن "
                "القرص الذي عليه المكتبة موصول."
            )
            structured.update(
                {"library": None, "tried": context.tried, "warnings_ar": ["المكتبة غير موجودة"]}
            )
            # A missing library is a diagnosis, not a tool failure.
            return reply("\n".join(lines), structured)

        library = context.library
        lines.append(f"✓ المكتبة: {library.root}  (طريقة التحديد: {_source_ar(library.source)})")
        structured["library"] = {
            "root": str(library.root),
            "source": library.source,
            "store_dir": str(library.store_dir),
        }

        # ---- catalogue ----
        try:
            totals = context.catalogue.totals()
            categories = context.catalogue.categories()
            lines.append(
                f"✓ فهرس الكتب (master.db): {totals['books']} كتابًا، "
                f"المنزَّل منها {totals['downloaded']}، "
                f"{len(categories)} قسمًا، {totals['authors']} مؤلفًا."
            )
            structured["catalogue"] = {**totals, "categories": len(categories)}
        except Exception as exc:
            lines.append(f"✗ فهرس الكتب (master.db): تعذّرت القراءة — {exc}")
            warnings.append("تعذّر قراءة master.db")
            structured["catalogue"] = None

        # ---- runtime files ----
        runtime = context.runtime
        if runtime and runtime.java_path:
            lines.append(f"✓ جافا (من ملفات الشاملة): {runtime.java_path}")
        else:
            lines.append("✗ جافا: لم تُوجد جافا التي تشحنها الشاملة.")
            warnings.append("جافا غير موجودة")
        if runtime and runtime.lucene_dir:
            lines.append(f"✓ ملفات Lucene: {runtime.lucene_dir}")
        else:
            lines.append("✗ ملفات Lucene: غير موجودة.")
            warnings.append("ملفات Lucene غير موجودة")

        if HELPER_JAR.is_file():
            lines.append(f"✓ ملف الوسيط: {HELPER_JAR.name} ({HELPER_JAR.stat().st_size // 1024} ك.ب)")
        else:
            lines.append(f"✗ ملف الوسيط مفقود: {HELPER_JAR}")
            warnings.append("ملف الوسيط (jar) مفقود")

        for problem in (runtime.problems_ar if runtime else ()):
            lines.append(f"  ملاحظة: {problem}")
            warnings.append(problem)

        structured["runtime"] = {
            "java_path": str(runtime.java_path) if runtime and runtime.java_path else None,
            "lucene_dir": str(runtime.lucene_dir) if runtime and runtime.lucene_dir else None,
            "helper_jar": str(HELPER_JAR) if HELPER_JAR.is_file() else None,
            "problems_ar": list(runtime.problems_ar) if runtime else [],
        }

        # ---- engine ----
        engine_info: dict[str, Any] | None = None
        try:
            started = time.time()
            engine = context.require_engine()
            info = engine.health(force=True)
            elapsed = (time.time() - started) * 1000
            lines.append("")
            lines.append(
                f"✓ محرك البحث: جافا {info.get('java_version')} — "
                f"Lucene {info.get('lucene_version')} — استجاب في {elapsed:.0f} م.ث"
            )
            lines.append(
                f"  فهرس الصفحات: {_number(info.get('page_docs'))} صفحة — "
                f"فهرس العناوين: {_number(info.get('title_docs'))} عنوان"
            )
            field = info.get("book_field")
            if field:
                lines.append(f"  حصر البحث في نطاق الكتب يعمل داخل الفهرس (الحقل {field}).")
            else:
                lines.append(
                    "  تنبيه: لم يُعرف حقل الكتاب في الفهرس، فحصر النطاق يجري بعد البحث "
                    "والعدد الإجمالي تقريبي."
                )
                warnings.append("حقل الكتاب غير معروف في الفهرس")
            engine_info = info
        except errors.ShamelaError as exc:
            lines.append("")
            lines.append(f"✗ محرك البحث: {exc.message_ar}")
            lines.append(f"  ما العمل: {exc.next_step_ar}")
            warnings.append("محرك البحث لا يعمل")
        structured["engine"] = engine_info

        # ---- roots ----
        roots = context.roots
        if roots.available:
            sample = roots.roots("الطلاق")
            if sample:
                lines.append(f"✓ قاعدة الجذور: تعمل (الطلاق ← {'، '.join(sample)}).")
            else:
                lines.append("✓ قاعدة الجذور: موجودة، ولم تُرجِع جذرًا لكلمة الاختبار.")
            structured["roots"] = {"available": True, "sample": list(sample)}
        else:
            lines.append(
                "✗ قاعدة الجذور (S2.db): غير متاحة — البحث بالجذر سيرجع إلى البحث الحرفي."
            )
            structured["roots"] = {"available": False, "sample": []}

        # ---- folding probes ----
        if engine_info is not None:
            lines.append("")
            lines.append("مطابقة قواعد التطبيع للفهرس:")
            probe_results, probe_warnings = _run_probes(context)
            for row in probe_results:
                mark = "✓" if row["passed"] else "✗"
                lines.append(
                    f"  {mark} {row['rule']} — «{row['present_term']}»: "
                    f"{_number(row['present_docs'])}، «{row['absent_term']}»: "
                    f"{_number(row['absent_docs'])}"
                )
            warnings.extend(probe_warnings)
            structured["folding_probes"] = probe_results

        # ---- settings ----
        lines.append("")
        lines.append(
            f"الإعدادات: مهلة الانتظار {_seconds_ar(context.settings.timeout_ms // 1000)}، "
            f"إغلاق المحرك عند السكون بعد {_minutes_ar(context.settings.idle_ms // 60000)}."
        )

        if warnings:
            lines.append("")
            lines.append("تنبيهات:")
            lines.extend(f"- {warning}" for warning in dict.fromkeys(warnings))
        else:
            lines.append("")
            lines.append("لا تنبيهات — المكتبة والفهرس جاهزان للبحث. ✅")

        structured["warnings_ar"] = list(dict.fromkeys(warnings))
        return reply("\n".join(lines), structured)


def _run_probes(context) -> tuple[list[dict[str, Any]], list[str]]:
    """Assert each folding rule against the live index."""
    terms: list[str] = []
    for _, present, absent in FOLDING_PROBES:
        terms.extend([present, absent])

    try:
        result = context.bridge.probe(index="page", field="body", terms=terms)
    except errors.ShamelaError:
        return [], ["تعذّر فحص قواعد التطبيع (محرك البحث لا يستجيب)"]

    frequencies = result.get("docfreqs", {})
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for rule, present, absent in FOLDING_PROBES:
        present_docs = int(frequencies.get(present, 0) or 0)
        absent_docs = int(frequencies.get(absent, 0) or 0)
        passed = present_docs > 0 and absent_docs == 0
        rows.append(
            {
                "rule": rule,
                "present_term": present,
                "present_docs": present_docs,
                "absent_term": absent,
                "absent_docs": absent_docs,
                "passed": passed,
            }
        )
        if not passed:
            warnings.append(
                f"قاعدة تطبيع لا تطابق الفهرس: {rule} — قد تُرجع بعض عمليات البحث "
                "نتائج فارغة بلا سبب ظاهر."
            )
    return rows, warnings


def _source_ar(source: str) -> str:
    return {
        "env": "من إعدادات كلود",
        "argument": "مسار مضبوط يدويًا",
        "search": "بحث تلقائي في الأقراص",
    }.get(source, source)


def _number(value: Any) -> str:
    return f"{value:,}".replace(",", "٬") if isinstance(value, int) else "—"


def _counted_ar(count: int, singular: str, dual: str, plural: str) -> str:
    """Arabic counted nouns: 1 takes the singular, 2 the dual, 3–10 the plural."""
    if count == 1:
        return singular
    if count == 2:
        return dual
    if 3 <= count <= 10:
        return f"{count} {plural}"
    return f"{count} {singular}"


def _seconds_ar(count: int) -> str:
    return _counted_ar(count, "ثانية", "ثانيتان", "ثوانٍ")


def _minutes_ar(count: int) -> str:
    return _counted_ar(count, "دقيقة", "دقيقتان", "دقائق")
