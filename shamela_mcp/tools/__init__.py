"""MCP tool registration."""

from __future__ import annotations


def register_all(mcp, context) -> None:
    """Register every tool against the FastMCP app."""
    from . import books, guide, health, pages, search

    search.register(mcp, context)
    books.register(mcp, context)
    pages.register(mcp, context)
    health.register(mcp, context)
    guide.register(mcp, context)
