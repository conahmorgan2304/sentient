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
    assert rt.feedback.score("light.hall", "offers") > 0.99


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
