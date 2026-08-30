"""Arabic counted nouns, so the tool's own prose is not the weakest text on the page.

Arabic agreement changes with the count: one, two, three-to-ten, then eleven and up
each take a different form of the noun. A server that hands scholars «6 موضعًا» has
already told them how carefully the rest of it was written, so the few nouns this
server counts out loud are formed here rather than interpolated raw.
"""

from __future__ import annotations


def counted(number: int, one: str, two: str, few: str, many: str) -> str:
    """Format ``number`` with the right form of its noun."""
    if number == 1:
        return one
    if number == 2:
        return two
    if 3 <= number <= 10:
        return f"{number} {few}"
    return f"{number} {many}"


def books(number: int) -> str:
    return counted(number, "كتاب واحد", "كتابان", "كتب", "كتابًا")


def places(number: int) -> str:
    """Places = matching pages, the unit search results are counted in."""
    return counted(number, "موضع واحد", "موضعان", "مواضع", "موضعًا")
