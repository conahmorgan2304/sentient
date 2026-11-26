"""What leaves the house (metadata only) and forgetting the last 48 hours."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.ai import async_generate
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.privacy import forget_recent

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async def generate(call: ServiceCall):
        return {"data": "ok"}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return entry


async def test_record_is_metadata_only(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await async_generate(hass, {"task_name": "Sentient: camera checks", "instructions": "Is the van there? SECRET",
                                "attachments": [{"media_content_id": "media-source://camera/camera.drive",
                                                 "media_content_type": "image/jpeg"}]})
    await async_generate(hass, {"task_name": "Sentient: briefing", "instructions": "Write it"})
    out = entry.runtime_data.ai.sent_summary()
    assert out["by_feature"]["camera checks"] == {"times": 1, "pictures": 1, "clips": 0, "documents": 0}
    assert out["totals"]["times"] == 2
    assert "SECRET" not in str(entry.runtime_data.ai.sent)  # never the content
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "privacy_log": False})
    await hass.async_block_till_done()
    assert entry.runtime_data.ai.sent == []
    await async_generate(hass, {"task_name": "Sentient: briefing", "instructions": "Write it"})
    assert entry.runtime_data.ai.sent == []


async def test_forget_the_last_48_hours(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.timeline.add("said", "An old remark.")
    old = rt.memory.add("The boiler was serviced in May.")
    freezer.tick(timedelta(days=3))
    rt.timeline.add("said", "A new remark.")
    new = rt.memory.add("Sam's parcel is coming Friday.")
    kept = rt.memory.add("Wi-Fi password is on the fridge.")
    rt.memory.pin(kept.id, True)
    rt.visitors.log.appendleft({"time": dt_util.utcnow().isoformat(), "description": "A courier"})
    gone = forget_recent(rt, 48)
    assert gone["timeline"] >= 1 and gone["memories"] == 1 and gone["visitors"] == 1
    texts = [i["text"] for i in rt.timeline.items]
    assert "A new remark." not in texts and "An old remark." in texts
    ids = {m.id for m in rt.memory.items}
    assert old.id in ids and kept.id in ids and new.id not in ids


async def test_forget_recent_clears_replaced_memory_text(hass):
    from custom_components.sentient.privacy import forget_recent
    from tests.test_heating_programmes import _setup

    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.memory.add("The heating is comfortable at 20 degrees")
    rt.memory.add("The heating is comfortable at 21 degrees")
    assert rt.memory.changed
    removed = forget_recent(rt)
    assert removed["memories"] == removed["memory_changes"] == 1
    assert not rt.memory.items and not rt.memory.changed


async def test_forget_waits_for_stores_and_preserves_the_journal_on_write_error(hass, hass_storage, monkeypatch):
    from homeassistant.util.file import WriteError

    from custom_components.sentient.privacy import async_forget_recent

    rt = (await _setup(hass)).runtime_data
    row = rt.timeline.add("said", "Private timeline entry")
    timeline_store = rt.timeline._store
    await timeline_store.async_save({"items": [row], "problems": {}})
    rt.memory.add("Private memory")
    memory_store = rt.memory._store
    await memory_store.async_save({"items": [item.as_dict() for item in rt.memory.items]})
    journal = rt.brain._forget_store

    async def disk_failure(*args):
        raise WriteError("Temporary storage failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(timeline_store, "_async_write_data", disk_failure)
        outcome = await async_forget_recent(rt)
    assert "database_error" in outcome
    assert hass_storage[journal.key]["data"]["pending"]
    assert hass_storage[timeline_store.key]["data"]["items"] == [row]
    assert hass_storage[memory_store.key]["data"]["items"] == []

    outcome = await async_forget_recent(rt)
    assert "database_error" not in outcome
    assert hass_storage[timeline_store.key]["data"]["items"] == []
    assert hass_storage[journal.key]["data"]["pending"] == []


async def test_forget_clears_dormant_clip_history(hass, hass_storage):
    from custom_components.sentient.privacy import async_forget_recent

    rt = (await _setup(hass)).runtime_data
    assert "clipwatch" in rt.dormant
    store = rt.clipwatch._store
    now = dt_util.utcnow()
    old = {"time": (now - timedelta(days=3)).isoformat(), "summary": "Old clip"}
    recent = {"time": now.isoformat(), "summary": "Private clip"}
    await store.async_save({"items": [recent, old]})
    result = await async_forget_recent(rt)
    assert result["clip_descriptions"] == 1
    assert hass_storage[store.key]["data"]["items"] == [old]
    assert "clipwatch" in rt.dormant
