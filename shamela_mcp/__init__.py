"""MCP server exposing the local Al-Maktaba Al-Shamela 4 library to Claude Desktop."""

from __future__ import annotations

import hashlib
from pathlib import Path

__version__ = "1.1.0"


def build_id() -> str:
    """A short fingerprint of the source files on disk right now.

    Claude Desktop starts this server once and keeps the process alive, so editing the
    code changes nothing until the app is fully quit and reopened. That gap is invisible
    from inside a chat: a user applies a fix, sees the old behaviour, and has no way to
    tell a failed fix from a stale process. Comparing this against the value captured at
    import turns "did my restart take effect?" into something health can answer.
    """
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.rglob("*.py")):
        try:
            digest.update(path.read_bytes())
        except OSError:  # a file vanishing mid-scan says nothing useful about staleness
            continue
    return digest.hexdigest()[:12]


#: Fingerprint of the code this process actually loaded, frozen at import.
RUNNING_BUILD = build_id()
