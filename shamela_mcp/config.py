"""Runtime configuration: environment overrides and stable constants.

Everything the user can influence from the Claude Desktop config arrives here as an
environment variable. Values that look like unresolved template placeholders
(``${user_config.x}``) are treated as unset -- a bug that once silently disabled
Java discovery in a sibling project.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass

# Bumping this invalidates every outstanding cursor, by design: a change in folding
# rules changes which pages match, so old pagination state is no longer meaningful.
NORMALIZER_VERSION = "shamela-mcp-1"
CURSOR_VERSION = 1

DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_IDLE_MS = 300_000

# Full page texts are returned by design (scholars need complete context), so search
# result counts stay modest and callers page through with a cursor instead.
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20

_PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")


def cleaned(value: str | None) -> str | None:
    """Return a usable string, or None for blank/unresolved-placeholder values."""
    if value is None:
        return None
    text = value.strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


def _env_int(name: str, default: int) -> int:
    raw = cleaned(os.environ.get(name))
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class Settings:
    library_dir: str | None
    java_path: str | None
    timeout_ms: int
    idle_ms: int
    log_level: str

    @property
    def env_report(self) -> dict[str, str]:
        """Per-variable state, surfaced by shamela_health for diagnosis."""
        report: dict[str, str] = {}
        for name in (
            "SHAMELA_MCP_DIR",
            "SHAMELA_MCP_JAVA",
            "SHAMELA_MCP_TIMEOUT_MS",
            "SHAMELA_MCP_IDLE_MS",
            "SHAMELA_MCP_LOG",
        ):
            raw = os.environ.get(name)
            if raw is None:
                report[name] = "unset"
            elif not raw.strip():
                report[name] = "empty"
            elif _PLACEHOLDER.match(raw.strip()):
                report[name] = "unresolved_placeholder"
            else:
                report[name] = "set"
        return report


def load_settings() -> Settings:
    return Settings(
        library_dir=cleaned(os.environ.get("SHAMELA_MCP_DIR")),
        java_path=cleaned(os.environ.get("SHAMELA_MCP_JAVA")),
        timeout_ms=_env_int("SHAMELA_MCP_TIMEOUT_MS", DEFAULT_TIMEOUT_MS),
        idle_ms=_env_int("SHAMELA_MCP_IDLE_MS", DEFAULT_IDLE_MS),
        log_level=(cleaned(os.environ.get("SHAMELA_MCP_LOG")) or "WARNING").upper(),
    )


def configure_logging(level: str) -> None:
    """Log to stderr only -- stdout carries the MCP protocol."""
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level, logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
