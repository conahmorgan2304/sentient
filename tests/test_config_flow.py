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
