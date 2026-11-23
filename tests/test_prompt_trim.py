"""1.17.1: the voice prompt says each rule once — the same rules, fewer repeats."""
from homeassistant.core import Context
from homeassistant.helpers import llm

from tests.test_heating_programmes import _setup


async def _prompt(hass):
    return (await llm.async_get_api(hass, "sentient", llm.LLMContext(
        platform="test", context=Context(), language="en", assistant="conversation", device_id=None))).api_prompt


async def test_done_only_when_it_worked_is_said_once(hass):
    entry = await _setup(hass, evidence_awareness=True)
    prompt = await _prompt(hass)
    assert "Never call an attempted action completed unless the device reported the requested result" in prompt
    assert "After controlling a device, only say it's done" not in prompt
    entry.runtime_data.options = {**entry.runtime_data.options, "evidence_awareness": False}
    prompt = await _prompt(hass)
    assert "After controlling a device, only say it's done if the tool result says it worked" in prompt
