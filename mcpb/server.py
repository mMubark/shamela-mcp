"""Entry point for the .mcpb bundle.

The bundle carries the Java helper next to this file, but the package itself may be
imported from anywhere: with the ``uv`` server type the host resolves dependencies into
an environment of its own, so ``shamela_mcp`` can end up outside the bundle entirely
while the jar stays inside it. Pinning the jar here -- from a path relative to *this*
file, which is always in the bundle -- is the one place that holds true in every layout.

Set SHAMELA_MCP_JAR yourself and this defers to it; the bundled jar is a default, not
an override.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent


def main() -> None:
    jar = BUNDLE / "java" / "shamela-mcp-helper.jar"
    if jar.is_file():
        os.environ.setdefault("SHAMELA_MCP_JAR", str(jar))

    # A bundle that ships the package alongside this script must be importable even
    # when the host installed nothing from pyproject.toml.
    if (BUNDLE / "shamela_mcp" / "__init__.py").is_file():
        sys.path.insert(0, str(BUNDLE))

    from shamela_mcp.server import main as serve

    serve()


if __name__ == "__main__":
    main()
