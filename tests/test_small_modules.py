"""Small helpers that everything else leans on."""

import pytest

from custom_components.sentient.numeric import finite_float
from custom_components.sentient.whatsnew import WhatsNew, _key


@pytest.mark.parametrize("value,expected", [
    ("21.5", 21.5), (3, 3.0), ("0", 0.0), ("nan", None), ("inf", None), ("-inf", None),
    ("unavailable", None), (None, None), ("", None), ([], None), (10**400, None),
])
def test_finite_float(value, expected):
    assert finite_float(value) == expected


def test_version_keys_sort_numerically():
    assert _key("0.98.12") > _key("0.98.9") > _key("0.88.2")
    assert _key("not.a.version") == (0,)


async def test_whats_new_lists_only_what_is_newer_than_last_seen(hass):
    wn = WhatsNew(hass, "x", {})
    wn.version, wn.seen = "0.88.2", "0.88.0"
    assert [r["version"] for r in wn.listing()["releases"]] == ["0.88.2", "0.88.1"]
    wn.seen = "0.88.2"
    assert wn.listing()["releases"] == []


async def test_whats_new_can_be_switched_off(hass):
    wn = WhatsNew(hass, "x", {"whats_new": False})
    wn.version, wn.seen = "0.88.2", "0.0.1"
    assert wn.listing()["releases"] == []
