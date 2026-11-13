"""Only an authenticated HA administrator can store custom-page layouts."""
from tests.test_websocket import _setup


async def test_admin_layout_get_save_reset(hass, hass_ws_client):
    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/layout"})
    assert (await ws.receive_json())["result"]["layout"] == {}
    await ws.send_json({"id": 2, "type": "sentient/layout", "action": "save",
                        "layout": {"mind": [{"id": "thinking", "visible": True}]}})
    res = await ws.receive_json()
    assert res["success"], res
    assert res["result"]["layout"]["mind"][0]["id"] == "thinking"
    await ws.send_json({"id": 3, "type": "sentient/layout", "action": "reset"})
    assert (await ws.receive_json())["result"]["layout"] == {}


async def test_read_only_user_can_read_but_cannot_change_layout(hass, hass_ws_client, hass_read_only_access_token):
    await _setup(hass)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": "sentient/layout"})
    assert (await ws.receive_json())["result"]["layout"] == {}
    await ws.send_json({"id": 2, "type": "sentient/layout", "action": "save", "layout": {}})
    assert (await ws.receive_json())["error"]["code"] == "unauthorized"
