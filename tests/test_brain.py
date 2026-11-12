"""Memory & learning: the database, the house ledger, expectations, beliefs, reactions and case memory."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.beliefs import confidence, find_by_itself, held_on, status
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.db import Database, fts_query
from custom_components.sentient.expectations import Expectations
from custom_components.sentient.ledger import describe


async def _setup(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _at(days_ago: int, hour: int, minute: int = 0) -> float:
    local = dt_util.now().replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)
    return local.timestamp()


# ----- the database ---------------------------------------------------------------------------
async def test_database_search_ranks_and_falls_back(hass: HomeAssistant, tmp_path):
    db = Database(hass, "x", str(tmp_path / "s.db"))
    await db.async_open()
    await db.call(lambda c: db.put_memory(c, "conversation", "1", "2026-11-01T10:00:00", "They: is the boiler making that noise again?"))
    await db.call(lambda c: db.put_memory(c, "timeline", "2", "2026-11-02T10:00:00", "Kettle finished"))
    found = await db.call(lambda c: db.search(c, ["boiler"]))
    assert found and found[0]["ref"] == "1"
    assert await db.call(lambda c: db.search(c, ["radiator", "boilers"]))  # prefix match and alternative wordings
    db.fts = False
    assert (await db.call(lambda c: db.search(c, ["kettle"])))[0]["ref"] == "2"
    assert fts_query(['he said "drop table"']) == '"he" OR "said"* OR "drop"* OR "table"*'
    db.close()


async def test_old_ledger_days_are_summarised_then_dropped(hass: HomeAssistant, tmp_path):
    db = Database(hass, "x", str(tmp_path / "s.db"))
    old, new = _at(100, 11), _at(1, 11)
    rows = [(old, "climate.heating", "Heating", "climate", None, "heat", "off", "device", None, None),
            (new, "climate.heating", "Heating", "climate", None, "heat", "off", "device", None, None)]
    await db.call(lambda c: db.add_changes(c, rows))
    assert await db.call(lambda c: db.prune_ledger(c, _at(90, 0))) == 1
    left = await db.call(lambda c: db.changes(c))
    assert len(left) == 1 and left[0]["ts"] == new
    summary = await db.call(lambda c: c.execute("SELECT changes, unexplained FROM ledger_days").fetchone())
    assert tuple(summary) == (1, 1)
    db.close()


# ----- the ledger: what caused each change -------------------------------------------------------
async def test_every_change_records_its_cause(hass: HomeAssistant):
    entry = await _setup(hass)
    rt = entry.runtime_data
    user = await hass.auth.async_create_user("Conah")
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"})
    hass.states.async_set("climate.heating", "heat", {"friendly_name": "Heating"})
    await hass.async_block_till_done()
    hass.states.async_set("climate.heating", "off", {"friendly_name": "Heating"})  # nothing in Home Assistant
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"}, context=Context(user_id=user.id))
    auto = Context()
    hass.bus.async_fire("automation_triggered", {"name": "Evening lights", "entity_id": "automation.evening"}, context=auto)
    await hass.async_block_till_done()
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"}, context=Context(parent_id=auto.id))
    hass.states.async_set("light.landing", "on", {"friendly_name": "Landing light"}, context=rt.own.context("motion lights"))
    voice = Context()
    rt.brain.voice_request(None, "turn the landing light off", voice, "kitchen speaker")
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"}, context=voice)
    await hass.async_block_till_done()
    rows = await rt.brain.ledger.async_changes(newest_first=False)
    causes = [(r["entity"], r["cause"], r["who"]) for r in rows]
    assert ("climate.heating", "device", None) in causes
    assert ("light.landing", "person", "Conah") in causes
    assert ("light.landing", "automation", "Evening lights") in causes
    assert ("light.landing", "sentient", "motion lights") in causes
    assert ("light.landing", "voice", "kitchen speaker") in causes
    heat = next(r for r in rows if r["entity"] == "climate.heating")
    assert "nothing in Home Assistant" in describe(heat)


async def test_fast_numbers_are_kept_as_notable_changes(hass: HomeAssistant):
    entry = await _setup(hass)
    hass.states.async_set("sensor.power", "100")
    await hass.async_block_till_done()
    for v in ("101", "102", "103", "200", "unavailable"):
        hass.states.async_set("sensor.power", v)
    await hass.async_block_till_done()
    rows = await entry.runtime_data.brain.ledger.async_changes(entities=["sensor.power"], newest_first=False)
    assert [r["new"] for r in rows] == ["101", "200", "unavailable"]


async def test_switched_off_the_ledger_records_nothing(hass: HomeAssistant):
    entry = await _setup(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "house_ledger": False})
    await hass.async_block_till_done()
    hass.states.async_set("light.x", "on")
    await hass.async_block_till_done()
    hass.states.async_set("light.x", "off")
    await hass.async_block_till_done()
    assert await entry.runtime_data.brain.db.call(lambda c: c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]) == 0


# ----- expectations ----------------------------------------------------------------------------
def _row(entity, new, ts, old="off", cause="person"):
    return {"entity": entity, "new": new, "old": old, "ts": ts, "domain": entity.split(".")[0], "cause": cause, "name": entity}


def test_expectations_know_routine_from_odd():
    model = Expectations()
    for day in range(1, 15):
        model.learn("light.landing", "on", _at(day, 22, 30))
        model.days[0 if dt_util.as_local(dt_util.utc_from_timestamp(_at(day, 12))).weekday() < 5 else 1] += 1
    routine = model.surprise(_row("light.landing", "on", _at(0, 22, 40)))
    odd = model.surprise(_row("light.landing", "on", _at(0, 3, 10)))
    assert routine is not None and routine < 0.3
    assert odd == 1.0
    assert model.surprise(_row("light.never_seen", "on", _at(0, 3))) is None  # still learning


def test_a_broken_sequence_is_spotted():
    model = Expectations()
    for day in range(1, 12):
        door, gate = _row("binary_sensor.back_door", "on", _at(day, 8)), _row("binary_sensor.gate", "on", _at(day, 8, 1))
        model.learn_sequence(door, [])
        model.learn_sequence(gate, [door])
    assert model.usual_before("binary_sensor.gate", "on") == ("binary_sensor.back_door=on", 1.0)
    alone = _row("binary_sensor.gate", "on", _at(0, 3))
    assert model.broken_sequence(alone, []) == "binary_sensor.back_door=on"
    assert model.broken_sequence(alone, [_row("binary_sensor.back_door", "on", _at(0, 3) - 30)]) is None


def test_a_day_in_the_house_scores_better_as_it_learns():
    """Replay a fortnight of the same household day: the share it expected rises, and an odd event stands out."""
    model = Expectations()
    routine = [("light.kitchen", "on", 7, 0), ("switch.kettle", "on", 7, 5), ("binary_sensor.back_door", "on", 8, 0),
               ("light.lounge", "on", 18, 0), ("light.landing", "on", 22, 30), ("light.lounge", "off", 23, 0)]
    first = last = None
    for day in range(14, -1, -1):
        stamp = dt_util.as_local(dt_util.utc_from_timestamp(_at(day, 12)))
        for entity, state, h, m in routine:
            model.observe(_row(entity, state, _at(day, h, m)), [])
        if day:
            model.new_day(stamp)
        score = model.score(stamp.date().isoformat())
        if score["judged"] and first is None:
            first = score["percent"]
        last = score["percent"]
    assert first is not None and last == 100 and last >= first
    odd = model.observe(_row("binary_sensor.back_door", "on", _at(0, 3, 12)), [])
    assert odd == 1.0
    assert model.score()["surprises"][-1]["entity"] == "binary_sensor.back_door"


# ----- unexplained changes ---------------------------------------------------------------------
async def test_an_unexplained_heating_change_is_linked_then_said_when_unusual(hass: HomeAssistant):
    entry = await _setup(hass)
    rt = entry.runtime_data
    now = dt_util.now()
    earlier = [((now - timedelta(days=d)).timestamp(), "climate.heating", "Heating", "climate", None, "heat", "off", "device", None, None)
               for d in (2, 1)]
    await rt.brain.db.call(lambda c: rt.brain.db.add_changes(c, earlier))
    hass.states.async_set("climate.heating", "heat", {"friendly_name": "Heating"})
    await hass.async_block_till_done()
    with patch.object(rt.speech, "async_say", AsyncMock()) as say:
        hass.states.async_set("climate.heating", "off", {"friendly_name": "Heating"})
        await hass.async_block_till_done(wait_background_tasks=True)
    item = next(i for i in rt.timeline.items if i.get("detail") == "unexplained change")
    assert "nothing in Home Assistant did it" in item["text"] and "third time this week" in item["text"]
    say.assert_not_called()  # still learning the heating: on the timeline, not said
    # once it knows the heating is never switched off around now, the next one is said
    for day in range(3, 13):
        rt.brain.expect.learn("climate.heating", "off", (now - timedelta(days=day, hours=8)).timestamp())
    rt.brain.expect.days = [10, 10]
    hass.states.async_set("climate.heating", "heat", {"friendly_name": "Heating"})
    await hass.async_block_till_done(wait_background_tasks=True)
    rt.brain._said_unexplained.clear()
    with patch.object(rt.speech, "async_say", AsyncMock()) as say:
        hass.states.async_set("climate.heating", "off", {"friendly_name": "Heating"})
        await hass.async_block_till_done(wait_background_tasks=True)
    say.assert_called_once()


async def test_locks_are_not_watched_unless_chosen(hass: HomeAssistant):
    entry = await _setup(hass)
    assert not entry.runtime_data.brain._important("lock.front")
    assert entry.runtime_data.brain._important("climate.heating")
    entry.runtime_data.brain.options = {**entry.runtime_data.brain.options, "unexplained_entities": ["lock.front"]}
    assert entry.runtime_data.brain._important("lock.front") and not entry.runtime_data.brain._important("climate.heating")


async def test_unexplained_changes_switched_off_are_recorded_not_said(hass: HomeAssistant):
    entry = await _setup(hass, unexplained_changes=False)
    hass.states.async_set("lock.front", "locked")
    await hass.async_block_till_done()
    hass.states.async_set("lock.front", "unlocked")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not [i for i in entry.runtime_data.timeline.items if i.get("detail") == "unexplained change"]
    assert await entry.runtime_data.brain.ledger.async_changes(entities=["lock.front"])


# ----- reactions -------------------------------------------------------------------------------
async def test_sentients_change_put_straight_back_counts_against_it(hass: HomeAssistant):
    entry = await _setup(hass)
    rt = entry.runtime_data
    hass.states.async_set("light.hall", "off")
    await hass.async_block_till_done()
    hass.states.async_set("light.hall", "on", context=rt.own.context("motion lights"))
    await hass.async_block_till_done()
    hass.states.async_set("light.hall", "off", context=Context())
    await hass.async_block_till_done()
    assert len(rt.brain.outcomes["bad"]) == 1
    assert rt.feedback.score("light.hall", "offers") > 0.49


# ----- beliefs ---------------------------------------------------------------------------------
def test_beliefs_form_from_repeats_and_are_tested():
    rows = [_row("climate.heating", "off", _at(d, 11, 10), cause="device") for d in (5, 4, 3)]
    found = find_by_itself(rows)
    assert len(found) == 1 and "around 11am" in found[0]["claim"]
    belief = {**found[0], "params": found[0]["params"]}
    assert held_on(belief, [_row("climate.heating", "off", _at(0, 12, 5), cause="device")])
    assert not held_on(belief, [_row("climate.heating", "off", _at(0, 18), cause="device")])
    assert confidence(6, 0) > 0.8 and status(6, 0) == "held" and status(0, 6) == "dropped"


async def test_overnight_learning_tests_beliefs_and_reports(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    rows = [(_at(d, 11, 10), "climate.heating", "Heating", "climate", None, "heat", "off", "device", None, None) for d in (4, 3, 2, 1)]
    await brain.db.call(lambda c: brain.db.add_changes(c, rows))
    report = await brain.async_think_overnight()
    assert report["formed"] == 1
    today = [(_at(0, 11, 20), "climate.heating", "Heating", "climate", None, "heat", "off", "device", None, None)]
    await brain.db.call(lambda c: brain.db.add_changes(c, today))
    report = await brain.async_think_overnight(dt_util.now() + timedelta(days=1))
    assert report["tested"]["held"] == 1
    status_ = await brain.async_status()
    assert status_["beliefs"][0]["claim"].startswith("climate.heating") or "Heating" in status_["beliefs"][0]["claim"]


# ----- case memory and surprise gating ------------------------------------------------------------
async def test_the_same_ai_job_twice_is_answered_from_case_memory(hass: HomeAssistant):
    entry = await _setup(hass)
    gate = entry.runtime_data.ai
    calls = []

    async def generate(call):
        calls.append(call)
        return {"data": "hello"}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response="only")
    job = {"task_name": "sentient manual", "instructions": "Say hello"}
    assert await gate.async_call(dict(job)) == {"data": "hello"}
    assert await gate.async_call(dict(job)) == {"data": "hello"}
    assert len(calls) == 1
    await gate.async_call({**job, "attachments": [{"media_content_id": "x"}]})
    assert len(calls) == 2  # pictures are never answered from memory
    hass.config_entries.async_update_entry(entry, options={**entry.options, "ai_case_memory": False})
    await hass.async_block_till_done()
    await gate.async_call(dict(job))
    assert len(calls) == 3 and not entry.runtime_data.brain.cases


async def test_surprise_gating_watches_before_it_skips(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    for day in range(1, 10):
        brain.expect.learn("switch.kettle", "off", dt_util.utcnow().timestamp() - day * 86400)
    brain.expect.days = [9, 9]
    record: dict = {}
    assert brain.gate_remark(record, [("switch.kettle", "off")]) is False and record["would_skip"] == "usual for this time"
    brain.options = {**brain.options, "surprise_gating": "on"}
    record = {}
    assert brain.gate_remark(record, [("switch.kettle", "off")]) is True and record["result"].startswith("skipped")
    assert brain.gate_remark({}, [("switch.unknown", "on")]) is False  # still learning: never skipped


# ----- voice speed ------------------------------------------------------------------------------
async def test_a_speaker_that_hears_slowly_is_flagged(hass: HomeAssistant, freezer):
    entry = await _setup(hass)
    rt = entry.runtime_data
    sat = "assist_satellite.lounge"
    for _ in range(2):
        hass.states.async_set(sat, "listening", {"friendly_name": "Lounge speaker"})
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=30))
        hass.states.async_set(sat, "processing", {"friendly_name": "Lounge speaker"})
        await hass.async_block_till_done()
        rt.brain.voice_request(sat, "what temperature", None)
        await hass.async_block_till_done(wait_background_tasks=True)
        hass.states.async_set(sat, "idle", {"friendly_name": "Lounge speaker"})
    item = next(i for i in rt.timeline.items if i.get("detail") == "voice speed")
    assert "slow to hear you" in item["text"]
    assert rt.brain.voice_issues()[0]["entity_id"] == sat


async def test_a_quick_reply_is_never_called_slow(hass: HomeAssistant, freezer):
    entry = await _setup(hass)
    sat = "assist_satellite.kitchen"
    for _ in range(3):
        hass.states.async_set(sat, "listening")
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=5))
        hass.states.async_set(sat, "processing")
        await hass.async_block_till_done()
        entry.runtime_data.brain.voice_request(sat, "turn the kitchen light off", None)
        hass.states.async_set(sat, "idle")
    assert not entry.runtime_data.brain.voice_issues()


# ----- recall through the voice -------------------------------------------------------------------
async def test_history_and_root_cause_answer_the_heating_question(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    hass.states.async_set("climate.heating", "heat", {"friendly_name": "Heating"})
    hass.states.async_set("sensor.thermostat_battery", "5")
    await hass.async_block_till_done()
    hass.states.async_set("climate.heating", "off", {"friendly_name": "Heating"})
    await hass.async_block_till_done()
    out = await brain.async_history("heating", 24)
    assert out["changes"][0]["to"] == "off" and "nothing in Home Assistant" in out["changes"][0]["caused_by"]
    why = await brain.async_root_cause("heating")
    assert why["conclusion"].startswith("Nothing in Home Assistant changed it")
    assert (await brain.async_history("the shed disco ball"))["error"]


async def test_the_voice_tools_respect_exposure_and_guests(hass: HomeAssistant):
    from homeassistant.helpers import llm

    from custom_components.sentient.llm_api import HistoryTool

    entry = await _setup(hass)
    hass.states.async_set("light.secret", "off")
    await hass.async_block_till_done()
    hass.states.async_set("light.secret", "on")
    await hass.async_block_till_done()
    ctx = llm.LLMContext(platform="test", context=Context(), language="en", assistant="conversation", device_id=None)
    tool = HistoryTool()
    with patch("custom_components.sentient.llm_api._exposed", return_value=False):
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={}), ctx)
    data = out.data if hasattr(out, "data") else out
    assert data["changes"] == []
    entry.runtime_data.speech.guest_mode = True
    out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={}), ctx)
    data = out.data if hasattr(out, "data") else out
    assert "guests" in data["error"]


async def test_recall_finds_older_conversations_in_the_database(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    await brain.db.call(lambda c: brain.db.put_memory(c, "conversation", "old", "2026-02-01T10:00:00", "Kitchen They: the boiler is making a banging noise"))
    found = await brain.async_search(["radiators", "boiler"], ["conversation", "timeline"])
    assert found and "banging" in found[0]["text"]


async def test_facts_and_the_timeline_are_copied_into_the_database(hass: HomeAssistant):
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.memory.add("The rabbits live in the hutch by the shed")
    rt.timeline.add("health", "The gate battery is low")
    await rt.brain.async_sync_memories()
    await hass.async_block_till_done(wait_background_tasks=True)
    kinds = await rt.brain.db.call(lambda c: dict(c.execute("SELECT kind, COUNT(*) FROM memories GROUP BY kind").fetchall()))
    assert kinds.get("fact") == 1 and kinds.get("timeline", 0) >= 1
    rt.memory.forget(rt.memory.items[0].id)
    await rt.brain.async_sync_memories()
    assert not await rt.brain.db.call(lambda c: c.execute("SELECT 1 FROM memories WHERE kind = 'fact'").fetchall())


# ----- the Mind tab -----------------------------------------------------------------------------
async def test_the_mind_card_and_clearing(hass: HomeAssistant, hass_ws_client):
    await _setup(hass)
    hass.states.async_set("light.x", "on")
    await hass.async_block_till_done()
    hass.states.async_set("light.x", "off")
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/brain"})
    out = (await ws.receive_json())["result"]
    assert out["on"] and out["ledger_on"] and out["ledger"] >= 1 and "prediction" in out and "independence" in out
    await ws.send_json({"id": 2, "type": "sentient/brain/clear", "what": "ledger"})
    assert (await ws.receive_json())["result"] == {"cleared": "ledger"}
    await ws.send_json({"id": 3, "type": "sentient/brain"})
    assert (await ws.receive_json())["result"]["ledger"] == 0


async def test_a_door_opening_without_its_usual_partner_is_noted(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    hass.states.async_set("binary_sensor.gate", "off", {"device_class": "door", "friendly_name": "Gate"})
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door", "friendly_name": "Back door"})
    await hass.async_block_till_done()
    for day in range(1, 12):
        door, gate = _row("binary_sensor.back_door", "on", _at(day, 8)), _row("binary_sensor.gate", "on", _at(day, 8, 1))
        brain.expect.learn_sequence(door, [])
        brain.expect.learn_sequence(gate, [door])
        brain.expect.learn("binary_sensor.gate", "on", _at(day, 8, 1))
    brain.expect.days = [11, 11]
    hass.states.async_set("binary_sensor.gate", "on", {"device_class": "door", "friendly_name": "Gate"})
    await hass.async_block_till_done()
    item = next((i for i in entry.runtime_data.timeline.items if i.get("detail") == "broken routine"), None)
    if dt_util.now().hour not in (7, 8, 9):
        assert item is not None and "without Back door first" in item["text"]


async def test_case_memory_never_reuses_fresh_jobs_and_privacy_clears_it(hass: HomeAssistant):
    from custom_components.sentient.privacy import forget_recent

    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    assert brain.case_key({"task_name": "Sentient: tomorrow", "instructions": "x"}) is None
    brain.case_put({"task_name": "sentient manual", "instructions": "x"}, {"data": "y"})
    assert brain.cases
    forget_recent(entry.runtime_data, 48)
    assert not brain.cases


async def test_a_cloud_device_catching_up_is_not_a_mystery(hass: HomeAssistant):
    from homeassistant.core import ServiceCall

    entry = await _setup(hass)
    hass.states.async_set("climate.heating", "heat", {"friendly_name": "Heating"})
    await hass.async_block_till_done()

    async def set_mode(call: ServiceCall) -> None:
        return None  # a cloud thermostat: nothing changes until its next poll

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    auto = Context()
    hass.bus.async_fire("automation_triggered", {"name": "Heating off at lunch"}, context=auto)
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": "climate.heating", "hvac_mode": "off"},
                                   blocking=True, context=Context(parent_id=auto.id))
    hass.states.async_set("climate.heating", "off", {"friendly_name": "Heating"})  # the poll, under a new context
    await hass.async_block_till_done()
    hass.states.async_set("climate.heating", "heat", {"friendly_name": "Heating"})  # later, by itself
    await hass.async_block_till_done()
    rows = await entry.runtime_data.brain.ledger.async_changes(entities=["climate.heating"], newest_first=False)
    assert [(r["new"], r["cause"]) for r in rows] == [("off", "automation"), ("heat", "device")]
    assert rows[0]["who"].startswith("Heating off at lunch") and "matched by time" in rows[0]["who"]


async def test_severe_warnings_alert_phones_only_when_dangerous(hass: HomeAssistant):
    entry = await _setup(hass, weather_warnings=True)
    w = entry.runtime_data.warnings
    with patch.object(entry.runtime_data.speech, "async_say", AsyncMock()):
        await w._announce_new([{"id": "1", "title": "Amber wind", "level": "amber", "source": "Met Office"},
                               {"id": "2", "title": "Red snow", "level": "red", "source": "Met Office"}])
    kinds = {i["text"]: i["kind"] for i in entry.runtime_data.timeline.items if i["text"].startswith("Met Office")}
    assert kinds == {"Met Office: Amber wind": "noticed", "Met Office: Red snow": "critical"}


async def test_the_nightly_checkpoint_really_runs(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    hass.states.async_set("light.x", "on")
    await hass.async_block_till_done()
    hass.states.async_set("light.x", "off")
    await hass.async_block_till_done()
    await brain.ledger.async_flush()
    report = await brain.async_think_overnight()
    assert report["checkpoint"][0] == 0  # 0 = not blocked by an open write
    assert (await brain.async_status())["disk_mb"] >= 0


async def test_a_failed_ledger_write_is_kept_and_retried(hass: HomeAssistant):
    entry = await _setup(hass)
    ledger = entry.runtime_data.brain.ledger
    hass.states.async_set("light.y", "on")
    await hass.async_block_till_done()
    hass.states.async_set("light.y", "off")
    await hass.async_block_till_done()
    with patch.object(ledger.db, "call", AsyncMock(side_effect=OSError("disk busy"))):
        await ledger.async_flush()
    assert [r["entity"] for r in ledger._buffer] == ["light.y"]
    await ledger.async_flush()
    assert not ledger._buffer and await ledger.async_changes(entities=["light.y"])


async def test_root_cause_only_shows_what_the_voice_may_see(hass: HomeAssistant):
    from homeassistant.helpers import area_registry as ar, entity_registry as er

    entry = await _setup(hass)
    hall = ar.async_get(hass).async_create("Hall").id
    reg = er.async_get(hass)
    for eid in ("climate.heating", "light.private_lamp"):
        d, o = eid.split(".")
        reg.async_get_or_create(d, "test", o, suggested_object_id=o)
        reg.async_update_entity(eid, area_id=hall)
        hass.states.async_set(eid, "off" if d == "light" else "heat")
    await hass.async_block_till_done()
    hass.states.async_set("light.private_lamp", "on")
    hass.states.async_set("climate.heating", "off")
    await hass.async_block_till_done()
    brain = entry.runtime_data.brain
    full = await brain.async_root_cause("climate.heating")
    assert any("private lamp" in x["line"] for x in full["timeline_before"])
    shown = await brain.async_root_cause("climate.heating", lambda e: e != "light.private_lamp")
    assert not any("private lamp" in x["line"] for x in shown["timeline_before"])


async def test_history_stays_private_for_an_unplaced_voice(hass: HomeAssistant):
    from homeassistant.helpers import llm

    from custom_components.sentient.llm_api import HistoryTool

    await _setup(hass)
    tool = HistoryTool()
    ctx = llm.LLMContext(platform="test", context=Context(), language="en", assistant="conversation", device_id=None)
    with patch("custom_components.sentient.voice_id.cautious_turn", return_value=True):
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={}), ctx)
    data = out.data if hasattr(out, "data") else out
    assert "isn't shared" in data["error"]


async def test_warnings_already_said_stay_said_after_a_restart(hass: HomeAssistant):
    entry = await _setup(hass, weather_warnings=True)
    amber = {"id": "w1", "title": "Amber wind", "level": "amber", "source": "Met Office"}
    with patch.object(entry.runtime_data.speech, "async_say", AsyncMock()) as say:
        await entry.runtime_data.warnings._announce_new([amber])
        await hass.async_block_till_done()
        await entry.runtime_data.warnings._store.async_save({"seen": entry.runtime_data.warnings.seen})
        from custom_components.sentient.warnings_feed import Warnings

        fresh = Warnings(hass, entry.runtime_data.options, lambda: entry.runtime_data, entry.entry_id)
        await fresh._announce_new([amber])
        assert say.call_count == 1
        await fresh._announce_new([{**amber, "level": "red"}])  # escalated: but already severe, so not again
        assert say.call_count == 1
        yellow = {"id": "w2", "title": "Yellow rain", "level": "yellow", "source": "Met Office"}
        await fresh._announce_new([yellow])
        await fresh._announce_new([{**yellow, "level": "amber"}])  # yellow to amber: said
        assert say.call_count == 2


async def test_clearing_learning_during_warm_up_stays_cleared(hass: HomeAssistant):
    import asyncio

    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_warm(_hass, model):
        started.set()
        await release.wait()
        model.learn("light.old", "on", _at(1, 8))
        model.warmed = True
        return 1

    brain.expect = Expectations()
    with patch("custom_components.sentient.brain.async_warm_up", slow_warm):
        await brain.async_apply()
        await started.wait()
        await brain.async_clear("learning")
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert "light.old" not in brain.expect.counts and not brain.warming


async def test_warm_up_adds_to_live_learning_without_losing_it(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain

    async def warm(_hass, model):
        model.learn("light.old", "on", _at(3, 8))
        model.days = [5, 2]
        model.warmed = True
        return 1

    brain.expect = Expectations()
    brain.expect.learn("light.live", "on", _at(0, 9))
    with patch("custom_components.sentient.brain.async_warm_up", warm):
        await brain.async_apply()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert {"light.old", "light.live"} <= set(brain.expect.counts) and brain.expect.days == [5, 2] and brain.expect.warmed


async def test_the_size_cap_keeps_daily_summaries(hass: HomeAssistant, tmp_path):
    db = Database(hass, "x", str(tmp_path / "s.db"))
    rows = [(_at(5, 10, i), "light.a", "A", "light", None, "off", "on", "device", None, None) for i in range(6)]
    await db.call(lambda c: db.add_changes(c, rows))
    await db.call(lambda c: db.prune_ledger(c, _at(90, 0), max_rows=2))
    assert len(await db.call(lambda c: db.changes(c))) == 2
    await db.call(lambda c: db.prune_ledger(c, _at(90, 0), max_rows=1))
    total = await db.call(lambda c: c.execute("SELECT SUM(changes), SUM(unexplained) FROM ledger_days").fetchone())
    assert tuple(total) == (5, 5)  # nothing dropped without being counted, and two nights added, not overwritten
    db.close()


async def test_a_damaged_database_is_set_aside_and_a_fresh_one_started(hass: HomeAssistant, tmp_path):
    path = tmp_path / "s.db"
    path.write_bytes(b"this is not a database" * 100)
    db = Database(hass, "x", str(path))
    await db.async_open()
    assert db.recovered and (tmp_path / db.recovered.split("/")[-1]).exists()
    await db.call(lambda c: db.put_memory(c, "timeline", "1", None, "fresh start"))
    assert await db.call(lambda c: db.search(c, ["fresh"]))
    db.close()


async def test_older_conversations_stay_out_when_memories_are_per_speaker(hass: HomeAssistant):
    from homeassistant.helpers import llm

    from custom_components.sentient.llm_api import RecallTool

    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    await brain.db.call(lambda c: brain.db.put_memory(c, "conversation", "p", "2026-02-01", "They: my secret boiler plan"))
    await brain.db.call(lambda c: brain.db.put_memory(c, "timeline", "t", "2026-02-01", "Boiler serviced"))
    tool = RecallTool()
    ctx = llm.LLMContext(platform="test", context=Context(), language="en", assistant="conversation", device_id=None)

    async def recall():
        out = await tool.async_call(hass, llm.ToolInput(tool_name=tool.name, tool_args={"query": "boiler"}), ctx)
        return (out.data if hasattr(out, "data") else out)["earlier"]

    assert {e["kind"] for e in await recall()} == {"conversation", "timeline"}
    entry.runtime_data.options = {**entry.runtime_data.options, "speaker_memories": True}
    assert {e["kind"] for e in await recall()} == {"timeline"}


async def test_diagnostics_and_doctor_show_the_database_without_names(hass: HomeAssistant):
    from custom_components.sentient.diagnostics import async_get_config_entry_diagnostics
    from custom_components.sentient.doctor import async_check

    entry = await _setup(hass, pronunciations="Conah = Connor", warnings_region="North West England")
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["brain"]["ledger_on"] is True and "disk_mb" in data["brain"]
    assert data["options"]["pronunciations"] == "**REDACTED**" and data["options"]["warnings_region"] == "**REDACTED**"
    rows = (await async_check(hass, entry.runtime_data))["rows"]
    assert any(r["name"] == "Memory database" and r["status"] == "ok" for r in rows)


async def test_warm_up_waits_for_home_assistant_to_finish_starting(hass: HomeAssistant):
    from homeassistant.core import CoreState

    hass.set_state(CoreState.starting)
    calls = []

    async def warm(_hass, model):
        calls.append(len(_hass.states.async_entity_ids()))
        model.warmed = True
        return 0

    with patch("custom_components.sentient.brain.async_warm_up", warm):
        entry = await _setup(hass)
        hass.states.async_set("light.loaded_later", "off")
        assert not calls
        hass.set_state(CoreState.running)
        hass.bus.async_fire("homeassistant_started")
        await hass.async_block_till_done(wait_background_tasks=True)
    assert calls and entry.runtime_data.brain.expect.warmed


async def test_forgetting_the_last_hours_reaches_the_database(hass: HomeAssistant):
    from custom_components.sentient.privacy import forget_recent

    entry = await _setup(hass)
    rt = entry.runtime_data
    brain = rt.brain
    now = dt_util.utcnow()
    old, recent = (now - timedelta(days=5)).isoformat(), (now - timedelta(hours=2)).isoformat()
    await brain.db.call(lambda c: brain.db.put_memory(c, "conversation", "old", old, "They: the old boiler chat"))
    await brain.db.call(lambda c: brain.db.put_memory(c, "conversation", "new", recent, "They: the secret boiler chat"))
    await brain.db.call(lambda c: brain.db.put_memory(c, "timeline", "t", recent, "Boiler noticed"))
    await brain.db.call(lambda c: brain.db.add_changes(c, [((now - timedelta(days=5)).timestamp(), "light.a", "A", "light", None, "off", "on", "person", None, None),
                                                           ((now - timedelta(hours=1)).timestamp(), "light.a", "A", "light", None, "on", "off", "person", None, None)]))
    hass.states.async_set("light.b", "off")
    await hass.async_block_till_done()
    hass.states.async_set("light.b", "on")  # waiting in the buffer, not yet written
    await hass.async_block_till_done()
    late = now.isoformat()
    forget_recent(rt, 48)
    await hass.async_block_till_done(wait_background_tasks=True)
    # a copy that was already on its way when the forgetting happened can't bring it back
    await brain.db.call(lambda c: brain.db.put_memory(c, "timeline", "late", late, "Boiler noticed again"))
    await brain.ledger.async_flush()
    texts = [r["text"] for r in await brain.async_search(["boiler"])]
    assert texts == ["They: the old boiler chat"]
    left = await brain.db.call(lambda c: brain.db.changes(c))
    assert [r["entity"] for r in left] == ["light.a"] and len(left) == 1
    # afterwards, new things are kept as normal
    await brain.db.call(lambda c: brain.db.put_memory(c, "timeline", "next", (dt_util.utcnow() + timedelta(minutes=5)).isoformat(), "Boiler fine"))
    assert "Boiler fine" in [r["text"] for r in await brain.async_search(["boiler"])]


async def test_clearing_the_ledger_cant_be_undone_by_a_waiting_write(hass: HomeAssistant):
    entry = await _setup(hass)
    brain = entry.runtime_data.brain
    hass.states.async_set("light.c", "off")
    await hass.async_block_till_done()
    hass.states.async_set("light.c", "on")
    await hass.async_block_till_done()
    taken = list(brain.ledger._buffer)  # a flush that had already taken its batch before the clear
    assert taken
    await brain.async_clear("ledger")
    tuples = [(r["ts"], r["entity"], r.get("name"), r.get("domain"), r.get("area"), r.get("old"), r.get("new"), r.get("cause"), r.get("who"), r.get("surprise")) for r in taken]
    await brain.db.call(lambda c: brain.db.add_changes(c, tuples))
    await brain.ledger.async_flush()
    assert await brain.db.call(lambda c: c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]) == 0
    # the timeline isn't touched by clearing the ledger
    await brain.db.call(lambda c: brain.db.put_memory(c, "timeline", "x", dt_util.utcnow().isoformat(), "Kettle finished"))
    assert await brain.async_search(["kettle"])
    hass.states.async_set("light.c", "off")  # and new changes are recorded again
    await hass.async_block_till_done()
    await brain.ledger.async_flush()
    assert await brain.db.call(lambda c: c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]) == 1
