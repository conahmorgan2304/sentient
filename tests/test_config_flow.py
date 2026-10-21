"""Setup wizard."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.sentient.const import DOMAIN


async def test_full_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"persona": "Nova"})
    assert result["step_id"] == "speech"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"speakers": [], "quiet_start": "21:00:00", "quiet_end": "08:00:00", "motion_window": 3,
         "banned_words": "sir", "suppress_entities": [], "suppress_seconds": 60, "prompt_memories": 25},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Nova"
    # only one brain per house
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_options_menu_and_memory_manager(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "speakers": []}, options={"motion_window": 7})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mem = entry.runtime_data.memory
    keep = mem.add("The bins go out on Thursday nights", "routine")
    drop = mem.add("The shed key is under the blue pot")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "memories"})
    assert result["step_id"] == "memories" and result["description_placeholders"]["count"] == "2"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"forget": [drop.id], "add": "Dinner is at six", "category": "routine", "edit": keep.id}
    )
    assert result["step_id"] == "edit_memory"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"text": "The bins go out on Wednesday nights", "category": "routine"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    texts = sorted(m.text for m in mem.items)
    assert texts == ["Dinner is at six", "The bins go out on Wednesday nights"]
    assert mem.get(keep.id).text.endswith("Wednesday nights")
    assert entry.options == {"motion_window": 7}  # settings untouched


async def test_options_settings(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"persona": "Nova", "speakers": [], "all_occupied": False, "quiet_start": "22:00:00", "quiet_end": "07:00:00",
         "motion_window": 10, "pass_through_areas": [], "banned_words": "sir", "suppress_entities": [],
         "suppress_seconds": 60, "prompt_memories": 25},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.speech.options["quiet_start"] == "22:00:00"
