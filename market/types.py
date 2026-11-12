"""Map free-text property types from brochures onto the coarse sectors used for yields."""

from __future__ import annotations

import re

SECTORS: tuple[str, ...] = ("retail", "office", "industrial", "leisure", "mixed")

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "retail": ("retail", "shop", "bank", "parade", "supermarket", "store"),
    "office": ("office",),
    "industrial": ("industrial", "warehouse", "depot", "logistics", "distribution", "trade counter",
                   "workshop", "factory"),
    "leisure": ("hotel", "restaurant", "leisure", "pub", "gym", "cinema", "car park"),
}


def sector(property_type: str | None) -> str | None:
    """'Retail, High Street Retail' -> retail; 'Retail and Office' -> mixed; unknown -> None."""
    if not property_type:
        return None
    text = property_type.lower().strip()
    if text in SECTORS:
        return text
    found = {s for s, words in _KEYWORDS.items()
             if any(re.search(rf"\b{re.escape(w)}", text) for w in words)}
    if not found:
        return None
    return found.pop() if len(found) == 1 else "mixed"
