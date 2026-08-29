"""Error taxonomy. Every message is Arabic-first and names the next useful action.

The Arabic text reaches the scholar through Claude, so each error must say what
happened and what to do -- never a bare code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LIBRARY_NOT_FOUND = "LIBRARY_NOT_FOUND"
ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
INDEX_BUSY = "INDEX_BUSY"
CURSOR_INVALID = "CURSOR_INVALID"
CURSOR_STALE = "CURSOR_STALE"
BOOK_NOT_FOUND = "BOOK_NOT_FOUND"
BOOK_NOT_DOWNLOADED = "BOOK_NOT_DOWNLOADED"
PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
TITLE_NOT_FOUND = "TITLE_NOT_FOUND"
CATEGORY_UNKNOWN = "CATEGORY_UNKNOWN"
QUERY_EMPTY = "QUERY_EMPTY"
QUERY_NOT_ARABIC = "QUERY_NOT_ARABIC"
BAD_ARGUMENT = "BAD_ARGUMENT"


@dataclass
class ShamelaError(Exception):
    code: str
    message_ar: str
    next_step_ar: str = ""
    detail_en: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        return f"{self.code}: {self.detail_en or self.message_ar}"

    def as_text(self) -> str:
        lines = [f"تعذّر تنفيذ الطلب: {self.message_ar}"]
        if self.next_step_ar:
            lines.append(f"ما العمل: {self.next_step_ar}")
        return "\n".join(lines)

    def as_structured(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message_ar": self.message_ar,
                "next_step_ar": self.next_step_ar,
                **({"data": self.data} if self.data else {}),
            },
        }


def library_not_found(tried: list[dict[str, Any]] | None = None) -> ShamelaError:
    return ShamelaError(
        code=LIBRARY_NOT_FOUND,
        message_ar=(
            "لم يُعثر على مجلد المكتبة الشاملة، وأدوات البحث في النصوص متوقفة حتى "
            "يصحَّح المسار."
        ),
        next_step_ar=(
            "إن كانت المكتبة على قرص خارجي فتأكد أنه موصول، ثم شغّل setup.bat مرة "
            "أخرى ليطلب منك المسار. وللتشخيص الكامل اطلب: افحص مكتبة الشاملة."
        ),
        detail_en="Shamela library root not found",
        data={"tried": tried or []},
    )


def query_empty() -> ShamelaError:
    return ShamelaError(
        code=QUERY_EMPTY,
        message_ar="نصّ البحث فارغ أو لا يحتوي كلمات قابلة للبحث.",
        next_step_ar="اكتب كلمة أو عبارة عربية للبحث عنها، مثل: أحكام صلاة المسافر.",
        detail_en="query produced no searchable tokens",
    )


def query_not_arabic(query: str) -> ShamelaError:
    return ShamelaError(
        code=QUERY_NOT_ARABIC,
        message_ar=(
            f"الاستعلام «{query}» لا يحتوي حروفًا عربية، ونصوص المكتبة كلها عربية "
            "فلن يطابق شيئًا."
        ),
        next_step_ar=(
            "أعد صياغة البحث بالعربية بالمصطلح الشرعي المقصود — مثلًا: "
            "«prayer rules» تصبح «أحكام الصلاة»."
        ),
        detail_en="query contains no Arabic letters",
        data={"query": query},
    )


def book_not_found(book_id: int | str) -> ShamelaError:
    return ShamelaError(
        code=BOOK_NOT_FOUND,
        message_ar=f"لا كتاب بالمعرّف {book_id} في هذه المكتبة.",
        next_step_ar=(
            "ابحث عن الكتاب باسمه أولًا عبر shamela_find_books، فإن تعددت المطابقات "
            "فاعرضها على المستخدم ليختار."
        ),
        detail_en=f"book_id {book_id} not in master.db",
        data={"book_id": book_id},
    )


def book_not_downloaded(book_id: int, book_name: str) -> ShamelaError:
    return ShamelaError(
        code=BOOK_NOT_DOWNLOADED,
        message_ar=(
            f"كتاب «{book_name}» موجود في فهرس مكتبتك لكن نصّه غير منزَّل، "
            "والبحث لا يقرأ إلا المنزَّل."
        ),
        next_step_ar=(
            "افتح تطبيق المكتبة الشاملة ونزّل الكتاب ثم أعد المحاولة. ولمعرفة حالة "
            "أي كتاب استعمل shamela_find_books."
        ),
        detail_en=f"book {book_id} has no per-book database on disk",
        data={"book_id": book_id, "book_name": book_name},
    )


def page_not_found(book_id: int, page_id: int, available: str | None = None) -> ShamelaError:
    extra = f" (المعرّفات المتاحة {available})" if available else ""
    return ShamelaError(
        code=PAGE_NOT_FOUND,
        message_ar=f"لا صفحة بالمعرّف {page_id} في هذا الكتاب{extra}.",
        next_step_ar=(
            "استعمل page_id من نتيجة بحث أو من shamela_get_toc، لا رقم الصفحة "
            "المطبوعة. وللصفحة المطبوعة مرّر part و page."
        ),
        detail_en=f"page {page_id} not in book {book_id}",
        data={"book_id": book_id, "page_id": page_id},
    )


def category_unknown(given: str, closest: list[dict[str, Any]]) -> ShamelaError:
    hint = "، ".join(f"{c['name']}={c['id']}" for c in closest) or "لا مقترحات"
    return ShamelaError(
        code=CATEGORY_UNKNOWN,
        message_ar=f"لا قسم باسم أو رقم «{given}» في هذه المكتبة. أقرب الأقسام: {hint}.",
        next_step_ar=(
            "اختر من قائمة الأقسام في وصف الأداة، أو استدعِ shamela_list_categories "
            "لعرض الأقسام بأرقامها."
        ),
        detail_en=f"category {given!r} did not resolve",
        data={"given": given, "closest": closest},
    )


def engine_unavailable(detail: str, data: dict[str, Any] | None = None) -> ShamelaError:
    return ShamelaError(
        code=ENGINE_UNAVAILABLE,
        message_ar=(
            "لم يتيسّر تشغيل محرك البحث (جافا وLucene التي تشحنها الشاملة). نصوص "
            "الكتب تُقرأ منه، فالبحث وعرض الصفحات متوقفان. أما أدوات الفهارس "
            "والبيانات (البحث عن الكتب، بطاقة الكتاب، الأقسام) فتعمل."
        ),
        next_step_ar=(
            "تأكد أن المكتبة الشاملة مثبتة تثبيتًا صحيحًا، ثم اطلب: افحص مكتبة "
            "الشاملة — ليعرض المسارات المجرَّبة وسبب الإخفاق."
        ),
        detail_en=detail,
        data=data or {},
    )


def index_busy(detail: str, shamela_running: bool = False) -> ShamelaError:
    cause = (
        "وتطبيق الشاملة يعمل الآن على الجهاز، "
        if shamela_running
        else ""
    )
    return ShamelaError(
        code=INDEX_BUSY,
        message_ar=(
            f"استغرقت قراءة الفهرس أطول من المعتاد بكثير، {cause}"
            "والأرجح أن تطبيق الشاملة ينزّل كتبًا أو يعيد بناء فهارسه الآن — "
            "والقراءة أثناء ذلك بطيئة جدًّا."
        ),
        next_step_ar=(
            "انتظر حتى ينتهي تطبيق الشاملة من التنزيل أو الفهرسة ثم أعد المحاولة "
            "نفسها. لا تُضيّق النطاق ولا تغيّر الاستعلام لهذا السبب."
        ),
        detail_en=detail,
        data={"shamela_running": shamela_running},
    )


def cursor_invalid(detail: str) -> ShamelaError:
    return ShamelaError(
        code=CURSOR_INVALID,
        message_ar="مؤشر المتابعة (cursor) غير صالح.",
        next_step_ar="أعد تنفيذ البحث من أوله بدون cursor.",
        detail_en=detail,
    )


def cursor_stale(reason_ar: str, detail: str) -> ShamelaError:
    return ShamelaError(
        code=CURSOR_STALE,
        message_ar=f"بطل مؤشر المتابعة: {reason_ar}",
        next_step_ar=(
            "أعد تنفيذ البحث نفسه من أوله بدون cursor؛ وسيُبنى ترقيم جديد. ونبّه "
            "المستخدم أن مواضع النتائج السابقة قد تتغير."
        ),
        detail_en=detail,
    )


def bad_argument(message_ar: str, next_step_ar: str, detail: str) -> ShamelaError:
    return ShamelaError(
        code=BAD_ARGUMENT,
        message_ar=message_ar,
        next_step_ar=next_step_ar,
        detail_en=detail,
    )
