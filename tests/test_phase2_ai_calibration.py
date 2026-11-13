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


async def test_ai_calibration_one_generic_call_only_with_budget_and_cooldown(hass, hass_ws_client):
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.options.update({"ai_routing_enabled": True, "ai_task_entity": "ai_task.main",
                       "ai_routing_alternative": "ai_task.alternative"})
    hass.states.async_set("ai_task.main", "idle")
    hass.states.async_set("ai_task.alternative", "idle")
    ws = await hass_ws_client(hass)
    with patch.object(rt.ai, "spend", return_value=True), patch.object(
        rt.ai, "async_call", new_callable=AsyncMock
    ) as call:
        call.return_value = {"data": "READY"}
        await ws.send_json({"id": 1, "type": "sentient/ai/routing/calibrate",
                            "action": "run", "provider": "alternative"})
        response = await ws.receive_json()
        assert response["success"], response
        sent = call.call_args.args[0]
        assert sent["entity_id"] == "ai_task.alternative"
        assert "household" not in sent["instructions"].lower()
        assert "READY" in sent["instructions"]
        await ws.send_json({"id": 2, "type": "sentient/ai/routing/calibrate",
                            "action": "run", "provider": "alternative"})
        retry = await ws.receive_json()
        assert retry["success"] is False
        assert retry["error"]["code"] == "rate_limited"
        call.assert_awaited_once()


async def test_demoted_job_recovers_only_after_explicit_admin_reset(hass, hass_ws_client):
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.options.update({"ai_routing_enabled": True, "ai_task_entity": "ai_task.main",
                       "ai_routing_alternative": "ai_task.alternative"})
    rt.ai._observed_by_family[("ai_task.alternative", "evening summary")] = [(False, 500)] * 12
    rt.ai._observed_by_family[("ai_task.main", "evening summary")] = [(True, 500)] * 12
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/ai/routing/calibrate",
                        "action": "status", "provider": "alternative"})
    result = await ws.receive_json()
    assert result["success"] and len(rt.ai._observed_by_family) == 2
    with patch.object(rt.ai, "async_call", new_callable=AsyncMock) as paid:
        await ws.send_json({"id": 2, "type": "sentient/ai/routing/calibrate",
                            "action": "reset_jobs", "provider": "alternative"})
        result = await ws.receive_json()
        assert result["success"] and result["result"]["job_penalties_reset"] is True
        assert ("ai_task.main", "evening summary") in rt.ai._observed_by_family
        assert ("ai_task.alternative", "evening summary") not in rt.ai._observed_by_family
        paid.assert_not_called()
