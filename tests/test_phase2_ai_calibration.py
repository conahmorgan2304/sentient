"""AI routing calibration is admin-only, disabled without both providers, no silent calls."""
from unittest.mock import AsyncMock, patch

from tests.test_websocket import _setup


async def test_ai_calibration_read_only_user_cannot_trigger_paid_calls(hass, hass_ws_client, hass_read_only_access_token):
    await _setup(hass)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    with patch("custom_components.sentient.ai.AIGate.async_call", new_callable=AsyncMock) as ai:
        await ws.send_json({"id": 1, "type": "sentient/ai/routing/calibrate",
                            "action": "run", "provider": "alternative"})
        assert (await ws.receive_json())["error"]["code"] == "unauthorized"
        ai.assert_not_called()


async def test_ai_calibration_needs_explicit_enabled_route(hass, hass_ws_client):
    await _setup(hass)
    ws = await hass_ws_client(hass)
    with patch("custom_components.sentient.ai.AIGate.async_call", new_callable=AsyncMock) as ai:
        await ws.send_json({"id": 1, "type": "sentient/ai/routing/calibrate",
                            "action": "run", "provider": "alternative"})
        result = await ws.receive_json()
        assert not result["success"]
        assert result["error"]["code"] == "not_configured"
        ai.assert_not_called()
