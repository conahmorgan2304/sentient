"""Clip watching on chosen cameras, faces from chosen cameras, and strangers forgotten — each working when on
and doing nothing when off."""

import json
import time
from types import SimpleNamespace

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}
URL = "http://frigate:5000"
START = int(time.time()) - 60
CLIP = f"{URL}/api/gate/start/{START}/end/{START + 13}/clip.mp4"


async def _setup(hass: HomeAssistant, tmp_path, **extra) -> MockConfigEntry:
    hass.config.media_dirs = {"local": str(tmp_path)}
    MockConfigEntry(domain="frigate", data={"url": URL}).add_to_hass(hass)
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    hass.states.async_set("notify.my_phone", "unknown")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


def _review(kind="end", severity="alert", zones=("Zone1",), camera="gate", metadata=None):
    start = START
    after = {"id": "rev1", "camera": camera, "start_time": start, "end_time": start + 12, "severity": severity,
             "data": {"detections": ["ev1"], "zones": list(zones), "objects": ["person"], "metadata": metadata}}
    return SimpleNamespace(payload=json.dumps({"type": kind, "after": after}))


WATCH = {"clip_watch": True, "clip_cameras": ["camera.gate"], "notify_targets": ["notify.my_phone"]}


async def test_gate_clip_watched_written_down_and_deleted(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    hass.states.async_set("camera.gate", "idle", {"friendly_name": "Gate"})
    entry = await _setup(hass, tmp_path, **WATCH, clip_notify="worth")
    aioclient_mock.get(CLIP,
                       content=b"\x00\x00\x00 ftypmp4 fake")
    ai = _ai(hass, {"summary": "A man walked a dog past the gate", "kind": "passer-by", "worth": False})
    phone = async_mock_service(hass, "notify", "mobile_app_my_phone")
    said = async_mock_service(hass, "assist_satellite", "announce")
    cw = entry.runtime_data.clipwatch
    cw._message(_review())
    await hass.async_block_till_done()
    assert ai and ai[0].data["attachments"][0]["media_content_type"] == "video/mp4"
    assert "whose" not in ai[0].data["structure"]  # names only when chosen
    item = cw.items[0]
    assert item["summary"] == "A man walked a dog past the gate." and item["event"] == "ev1"
    assert any(i["kind"] == "clip" and "a man walked a dog past" in i["text"] for i in entry.runtime_data.timeline.items)
    assert not (tmp_path / "sentient" / "clipwatch_gate.mp4").exists()  # deleted once watched
    assert not phone and not said  # not worth a look: nothing sent, and never spoken
    # the voice answers from what's already been watched — no new AI call
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                assistant=None, device_id=None))
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_what_happened", tool_args={"camera": "gate"}))
    assert len(ai) == 1 and out["already_watched"][0]["what"] == "A man walked a dog past the gate."


async def test_scope_cooldown_and_notify(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    hass.states.async_set("camera.gate", "idle", {"friendly_name": "Gate"})
    entry = await _setup(hass, tmp_path, **WATCH, clip_notify="suspicious")
    aioclient_mock.get(CLIP,
                       content=b"\x00 clip")
    ai = _ai(hass, {"summary": "Someone tried the car doors", "kind": "suspicious", "worth": True})
    phone = async_mock_service(hass, "notify", "mobile_app_my_phone")
    cw = entry.runtime_data.clipwatch
    cw._message(_review(severity="detection"))  # alerts only: a plain detection is skipped
    await hass.async_block_till_done()
    assert not ai and cw.skipped["not an alert"] == 1
    cw._message(_review())
    await hass.async_block_till_done()
    assert len(ai) == 1 and phone and phone[-1].data["data"]["image"] == "/api/frigate/notifications/ev1/thumbnail.jpg"
    cw._message(_review())  # the same camera again straight away: cooldown
    await hass.async_block_till_done()
    assert len(ai) == 1 and cw.skipped["cooldown"] == 1
    cw.options = {**cw.options, "clip_scope": "zones"}
    cw._last.clear()
    cw._message(_review(severity="detection", zones=()))
    await hass.async_block_till_done()
    assert len(ai) == 1 and cw.skipped["not in a zone"] == 1


async def test_frigate_summaries_need_no_ai(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **WATCH, clip_source="frigate")
    ai = _ai(hass, {})
    cw = entry.runtime_data.clipwatch
    cw._message(_review())  # the end of the review: waits for Frigate's summary
    cw._message(_review(kind="genai", metadata={"shortSummary": "A courier left a parcel at the gate and drove off",
                                                 "potential_threat_level": 0}))
    await hass.async_block_till_done()
    assert not ai and cw.items[0]["summary"].startswith("A courier left a parcel")


async def test_whose_from_looks_when_the_face_is_hidden(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **WATCH, clip_whose=True,
                         clip_looks="Alex: black motorbike helmet and riding jacket, on the motorbike")
    aioclient_mock.get(f"{URL}/api/faces", json={"train": [], "Alex": ["Alex-1.webp"]})
    aioclient_mock.get(f"{URL}/clips/faces/Alex/Alex-1.webp", content=b"RIFF webp")
    aioclient_mock.get(CLIP,
                       content=b"\x00 clip")
    ai = _ai(hass, {"summary": "Someone in a black helmet rode out of the gate on a motorbike", "kind": "household",
                    "worth": False, "whose": "alex"})
    cw = entry.runtime_data.clipwatch
    cw._message(_review())
    await hass.async_block_till_done()
    data = ai[0].data
    assert "motorbike helmet" in data["instructions"] and "whose" in data["structure"]
    assert [a["media_content_type"] for a in data["attachments"]] == ["video/mp4", "image/webp"]
    assert cw.items[0]["whose"] == "Alex"
    assert "(probably Alex)" in entry.runtime_data.timeline.items[0]["text"]


async def test_clip_watch_off(hass: HomeAssistant, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, clip_cameras=["camera.gate"])
    ai = _ai(hass, {})
    entry.runtime_data.clipwatch._message(_review())
    await hass.async_block_till_done()
    assert not ai and not entry.runtime_data.clipwatch.items


async def test_strangers_forgotten(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    now = time.time()
    old = f"{now - 90000:.1f}-abc123-{now - 90000:.1f}-unknown-0.31.webp"
    new = f"{now - 600:.1f}-def456-{now - 600:.1f}-Sam-0.55.webp"
    entry = await _setup(hass, tmp_path, face_forget_hours=24)
    aioclient_mock.get(f"{URL}/api/faces", json={"train": [old, new], "Sam": ["c.webp"]})
    aioclient_mock.post(f"{URL}/api/faces/train/delete", json={"success": True})
    out = await entry.runtime_data.faces.async_forget_old()
    assert out == {"forgotten": 1, "kept": 1}
    posted = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posted[-1][2] == {"ids": [old]}


async def test_not_someone_we_know_deletes_the_face(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    train = "1793190000.5-abc123-1793190001.2-unknown-0.30.webp"
    entry = await _setup(hass, tmp_path, face_teach=True, face_teach_cameras=["camera.front_door_doorbell"],
                         notify_targets=["notify.my_phone"])
    phone = async_mock_service(hass, "notify", "mobile_app_my_phone")
    aioclient_mock.get(f"{URL}/api/faces", json={"train": [train], "Alex": [], "Sam": []})
    aioclient_mock.get(f"{URL}/api/events", json=[{"id": "1793190000.5-abc123"}])
    aioclient_mock.post(f"{URL}/api/faces/train/delete", json={"success": True})
    await entry.runtime_data.faces.async_ask("front_door_doorbell")
    actions = phone[-1].data["data"]["actions"]
    assert [a["title"] for a in actions] == ["Alex", "Sam", "Someone else…"]  # three at most, "someone else" always there
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions[-1]["action"], "reply_text": "No"})
    await hass.async_block_till_done()
    posted = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert str(posted[-1][1]).endswith("/faces/train/delete") and posted[-1][2] == {"ids": [train]}


async def test_forgetting_off_and_names_only_from_chosen_cameras(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    hass.states.async_set("sensor.frigate_sam_last_camera", "None", {"friendly_name": "Frigate Sam Last Camera"})
    hass.states.async_set("camera.front_door_doorbell", "idle", {"friendly_name": "Front door doorbell"})
    entry = await _setup(hass, tmp_path, faces=True, face_cameras=["camera.front_door_doorbell"])
    assert "skipped" in await entry.runtime_data.faces.async_forget_old()
    assert not aioclient_mock.mock_calls  # off: Frigate isn't touched
    hass.states.async_set("sensor.frigate_sam_last_camera", "gate", {"friendly_name": "Frigate Sam Last Camera"})
    await hass.async_block_till_done()
    assert entry.runtime_data.faces.recent() == []  # the gate isn't chosen
    hass.states.async_set("sensor.frigate_sam_last_camera", "Front Door Doorbell", {"friendly_name": "Frigate Sam Last Camera"})
    await hass.async_block_till_done()
    assert [s["name"] for s in entry.runtime_data.faces.recent()] == ["Sam"]


async def test_h265_camera_uses_frigates_preview(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **WATCH)
    aioclient_mock.get(CLIP, content=b"\x00\x00\x00 ftypisom....hev1....")
    aioclient_mock.get(CLIP.replace("clip.mp4", "preview.mp4"), content=b"\x00\x00\x00 ftypisom avc1 preview")
    aioclient_mock.get(f"{URL}/api/events/ev1/snapshot-clean.webp", content=b"RIFF webp")
    ai = _ai(hass, {"summary": "A woman walked through the gate", "kind": "visitor", "worth": True})
    cw = entry.runtime_data.clipwatch
    cw._message(_review())
    await hass.async_block_till_done()
    assert [a["media_content_type"] for a in ai[0].data["attachments"]] == ["video/mp4", "image/webp"]
    assert "gate" in cw.hevc and cw.items[0]["summary"] == "A woman walked through the gate."
    assert not list((tmp_path / "sentient").glob("clipwatch_*"))


async def test_extras_refused_then_the_clip_alone(hass: HomeAssistant, aioclient_mock, tmp_path) -> None:
    entry = await _setup(hass, tmp_path, **WATCH)
    aioclient_mock.get(CLIP, content=b"....hev1....")
    aioclient_mock.get(CLIP.replace("clip.mp4", "preview.mp4"), content=b"avc1 preview")
    aioclient_mock.get(f"{URL}/api/events/ev1/snapshot-clean.webp", content=b"RIFF webp")
    calls = []

    async def generate(call: ServiceCall):
        calls.append(call)
        if len(call.data["attachments"]) > 1:
            raise ValueError("Request contains an invalid argument.")
        return {"conversation_id": "x", "data": {"summary": "A man walked past", "kind": "passer-by", "worth": False}}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    entry.runtime_data.clipwatch._message(_review())
    await hass.async_block_till_done()
    assert len(calls) == 2 and entry.runtime_data.clipwatch.items[0]["summary"] == "A man walked past."
