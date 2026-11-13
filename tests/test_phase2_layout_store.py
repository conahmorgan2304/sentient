"""Saving a layout is admin-only, bounded, and off means default layout."""
from types import SimpleNamespace

from custom_components.sentient.custom_layout_store import CustomLayoutStore


async def test_optional_custom_layout_saved_only_by_admin(hass):
    store = CustomLayoutStore(hass, "phase2-layout")
    await store.async_load()
    layout = {"mind": [{"id": "thinking", "visible": True}]}
    assert not (await store.async_save(layout, user=SimpleNamespace(is_admin=False)))["accepted"]
    assert not (await store.async_save(layout, user={"is_admin": True}))["accepted"]
    assert (await store.async_save(layout, user=SimpleNamespace(is_admin=True)))["saved"]
    assert store.view(enabled=True)["enabled"] is False
    assert (await store.async_set_enabled(True, user=SimpleNamespace(is_admin=True)))["accepted"]
    assert store.view(enabled=False)["layout"] == {}
    assert store.view(enabled=False)["saved_layout_present"]
    assert store.view(enabled=True)["layout"] == layout
    assert (await store.async_set_enabled(False, user=SimpleNamespace(is_admin=True)))["accepted"]
    assert store.view(enabled=True)["layout"] == {}
    assert (await store.async_set_enabled(True, user=SimpleNamespace(is_admin=True)))["accepted"]
    restored = CustomLayoutStore(hass, "phase2-layout")
    await restored.async_load()
    assert restored.view(enabled=True)["layout"] == layout
    assert not (await restored.async_reset(user=SimpleNamespace(is_admin=False)))["accepted"]
    assert (await restored.async_reset(user=SimpleNamespace(is_admin=True)))["saved"]
    assert restored.view(enabled=True)["layout"] == {}
