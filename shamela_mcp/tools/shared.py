"""Shared plumbing for tool handlers: error envelopes and reply shaping."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from mcp.types import CallToolResult, TextContent

from .. import errors

log = logging.getLogger(__name__)


def reply(text: str, structured: dict[str, Any]) -> CallToolResult:
    """A tool reply.

    The Arabic text is the channel Claude reads and quotes from, so it is delivered as
    text rather than as JSON: a serialised dict would arrive escaped and unreadable,
    and page texts would be paid for twice. The structured mirror carries ids and
    citation fields for clients that want to process results.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={"ok": True, **structured},
    )


def error_reply(exc: errors.ShamelaError) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=exc.as_text())],
        structuredContent=exc.as_structured(),
        isError=True,
    )


def tool(name: str) -> Callable:
    """Wrap a handler so every failure becomes an Arabic answer, not a stack trace."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
            try:
                return func(*args, **kwargs)
            except errors.ShamelaError as exc:
                log.info("%s failed: %s", name, exc)
                return error_reply(exc)
            except Exception as exc:  # pragma: no cover - unexpected only
                log.exception("%s raised", name)
                return error_reply(
                    errors.ShamelaError(
                        code="INTERNAL_ERROR",
                        message_ar=f"حدث خطأ غير متوقّع أثناء تنفيذ {name}.",
                        next_step_ar=(
                            "أعد المحاولة، وإن تكرّر الخطأ فاطلب: افحص مكتبة الشاملة — "
                            "لعرض حالة المكتبة والفهرس."
                        ),
                        detail_en=f"{type(exc).__name__}: {exc}",
                    )
                )

        return wrapper

    return decorator


def clamp(value: int | None, low: int, high: int, default: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))
