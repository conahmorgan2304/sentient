"""The optional update checker raises a Home Assistant repair while a newer stable release is out."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.updates import RELEASE_URL


async def _setup(hass, **extra):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN,
                            data={"persona": "Nova", "speakers": [], **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_repair_while_behind_and_gone_when_off(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(RELEASE_URL, json={"tag_name": "v99.0.0"})
    entry = await _setup(hass, update_checker=True)
    await entry.runtime_data.updates.async_check()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "update_available")
    assert issue is not None and issue.translation_placeholders["latest"] == "99.0.0"
    hass.config_entries.async_update_entry(entry, options={"update_checker": False})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "update_available") is None


async def test_no_repair_when_up_to_date(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(RELEASE_URL, json={"tag_name": "v0.0.1"})
    entry = await _setup(hass, update_checker=True)
    await entry.runtime_data.updates.async_check()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "update_available") is None
