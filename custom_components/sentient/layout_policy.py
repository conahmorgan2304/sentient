"""Pure validation for future optional customisable-page layouts.

This validates user-saved structure but does not activate editor UI or HA cards.
Only a server-side authenticated administrator can be allowed to save.
"""
from __future__ import annotations
from typing import Any
import re

PAGES = frozenset({"overview", "house", "mind", "security"})
MAX_CARDS = 50
_CARD_ID = re.compile(r"[a-z][a-z0-9_-]{0,79}\\Z")

def validate(layout: dict[str, Any], *, admin: bool) -> dict[str, Any]:
    if not admin:
        return {"accepted": False, "reason": "administrator_required"}
    if not isinstance(layout, dict) or set(layout) - PAGES:
        return {"accepted": False, "reason": "invalid_page"}
    for page, cards in layout.items():
        if not isinstance(cards, list) or len(cards) > MAX_CARDS:
            return {"accepted": False, "reason": "invalid_card_count"}
        keys = set()
        for card in cards:
            if (not isinstance(card, dict) or not {"id", "visible"} <= set(card)
                    or set(card) - {"id", "visible", "lane"}
                    or not isinstance(card.get("id"), str) or len(card["id"]) > 80
                    or not _CARD_ID.fullmatch(card["id"])
                    or not isinstance(card.get("visible"), bool)
                    or ("lane" in card and (type(card["lane"]) is not int or not 0 <= card["lane"] <= 2))
                    or card["id"] in keys):
                return {"accepted": False, "reason": "invalid_card"}
            keys.add(card["id"])
    return {"accepted": True, "reason": "validated_only",
            "does_not_grant_card_access": True, "does_not_save": True}
