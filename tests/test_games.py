"""Story games: a memory of their own, shown only while a game is on, and everything off when switched off."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.games import IDLE_HOURS, MAX_ENTRIES, MAX_LOG

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra):
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass: HomeAssistant):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))


async def _call(api, name: str, **args):
    return await api.async_call_tool(llm.ToolInput(tool_name=name, tool_args=args))


def _names(api) -> set[str]:
    return {t.name for t in api.tools}


async def test_off_by_default_means_no_tools_no_prompt_and_no_page(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    api = await _api(hass)
    assert not {"sentient_game", "sentient_game_set", "sentient_game_log"} & _names(api)
    assert "game" not in api.api_prompt.lower().replace("sentient_game", "")
    assert entry.runtime_data.games.prompt_lines() == []
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sentient/games"})
    assert (await client.receive_json())["result"] == {"enabled": False, "games": []}
    await client.send_json({"id": 2, "type": "sentient/games/edit", "op": "save", "name": "x"})
    assert (await client.receive_json())["success"] is False


async def test_game_tools_only_appear_while_a_game_is_being_played(hass: HomeAssistant) -> None:
    await _setup(hass, games=True)
    api = await _api(hass)
    assert "sentient_game" in _names(api) and "sentient_game_set" not in _names(api)
    assert "sentient_game" in api.api_prompt  # how to start one
    made = await _call(api, "sentient_game", action="new", game="Escape the flat", setting="A locked flat", rules="Short replies")
    assert made.get("created_and_started") == "Escape the flat"
    api = await _api(hass)
    assert {"sentient_game_set", "sentient_game_log"} <= _names(api)
    assert "Escape the flat" in api.api_prompt and "A locked flat" in api.api_prompt and "fresh start" in api.api_prompt
    out = await _call(api, "sentient_game", action="stop")
    assert out["stopped"] == "Escape the flat"
    api = await _api(hass)
    assert "sentient_game_set" not in _names(api) and "A locked flat" not in api.api_prompt


async def test_state_is_exact_carries_over_and_is_shown_to_the_voice(hass: HomeAssistant) -> None:
    entry = await _setup(hass, games=True)
    api = await _api(hass)
    await _call(api, "sentient_game", action="new", game="Escape the flat")
    api = await _api(hass)
    await _call(api, "sentient_game_set", kind="place", key="where", value="kitchen")
    await _call(api, "sentient_game_set", kind="item", key="Brass key", value="in the drawer")
    await _call(api, "sentient_game_set", kind="item", key="brass key", value="in my pocket")  # the same fact, not a second one
    await _call(api, "sentient_game_set", kind="flag", key="front door", value="locked")
    await _call(api, "sentient_game_set", kind="flag", key="front door", value="unlocked")  # replaces: this is the point
    await _call(api, "sentient_game_log", text="They took the key and went to the hall")
    game = entry.runtime_data.games.active
    assert sorted((e["kind"], e["key"], e["value"]) for e in game["entries"]) == [
        ("flag", "front door", "unlocked"), ("item", "brass key", "in my pocket"), ("place", "where", "kitchen")]
    await _call(api, "sentient_game", action="stop")
    out = await _call(api, "sentient_game", action="start", game="escape flat")  # another assistant, another day
    assert out["started"] == "Escape the flat" and out["memory"]["item"] == ["brass key: in my pocket"]
    assert out["story_so_far"] == ["They took the key and went to the hall"]
    api = await _api(hass)
    assert "brass key: in my pocket" in api.api_prompt and "front door: unlocked" in api.api_prompt
    gone = await _call(api, "sentient_game_set", kind="item", key="brass key", remove=True)
    assert gone == {"removed": True} and "brass key" not in (await _api(hass)).api_prompt


async def test_game_tools_refuse_when_no_game_is_on(hass: HomeAssistant) -> None:
    await _setup(hass, games=True)
    api = await _api(hass)
    with pytest.raises(HomeAssistantError, match="not found"):  # the tool isn't even offered
        await _call(api, "sentient_game_set", kind="item", key="x", value="y")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    entry.runtime_data.games.save_game("Paused")
    entry.runtime_data.games.start("paused")
    games_api = await _api(hass)
    entry.runtime_data.games.stop()  # put away after this conversation was given its tools
    assert "no story game" in str(await _call(games_api, "sentient_game_set", kind="item", key="x", value="y"))


async def test_game_memory_is_separate_from_the_houses_memory(hass: HomeAssistant) -> None:
    entry = await _setup(hass, games=True)
    rt = entry.runtime_data
    rt.memory.add("Siobhan likes jazz", "preference")
    api = await _api(hass)
    await _call(api, "sentient_game", action="new", game="Heist")
    api = await _api(hass)
    await _call(api, "sentient_game_set", kind="item", key="crowbar", value="in the van")
    assert all("crowbar" not in m.text for m in rt.memory.items)  # nothing the game does reaches the house
    assert "sentient_recall" in _names(api)  # but the house's memory can be read for the game
    found = await _call(api, "sentient_recall", query="jazz")
    assert "Siobhan likes jazz" in str(found)
    assert "crowbar" not in str(await _call(api, "sentient_recall", query="crowbar"))  # and the game stays out of it


async def test_a_full_game_says_so_and_the_log_drops_its_oldest_line(hass: HomeAssistant) -> None:
    entry = await _setup(hass, games=True)
    games = entry.runtime_data.games
    game = games.save_game("Big one")
    for n in range(MAX_ENTRIES):
        games.set_entry(game["id"], "note", f"n{n}", "x")
    with pytest.raises(ValueError, match="full"):
        games.set_entry(game["id"], "note", "one too many", "x")
    games.set_entry(game["id"], "note", "n0", "changed")  # changing a fact is not adding one
    for n in range(MAX_LOG + 5):
        games.log(game["id"], f"move {n}")
    assert len(game["log"]) == MAX_LOG and game["log"][0]["text"] == "move 5"


async def test_an_untouched_game_is_put_away_but_keeps_its_save(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, games=True)
    games = entry.runtime_data.games
    game = games.save_game("Night shift")
    games.set_entry(game["id"], "place", "where", "roof")
    games.start("night shift")
    assert games.active is game
    freezer.tick(timedelta(hours=IDLE_HOURS, minutes=1))
    assert games.active is None and games.games[game["id"]]["entries"]


async def test_switching_off_hides_everything_but_keeps_the_saved_games(hass: HomeAssistant) -> None:
    entry = await _setup(hass, games=True)
    games = entry.runtime_data.games
    game = games.save_game("Keep me")
    games.set_entry(game["id"], "item", "lamp", "lit")
    games.start("keep me")
    hass.config_entries.async_update_entry(entry, data={**entry.data, "games": False})
    games.options = {**entry.data}
    assert games.active is None and games.prompt_lines() == [] and games.start("keep me") is None
    games.options = {**entry.data, "games": True}
    assert games.games[game["id"]]["entries"][0]["key"] == "lamp"  # still there when it is switched back on


async def test_the_page_edits_games_and_their_memory(hass: HomeAssistant, hass_ws_client) -> None:
    await _setup(hass, games=True)
    client = await hass_ws_client(hass)

    async def edit(**msg):
        await client.send_json({"id": edit.n, "type": "sentient/games/edit", **msg})
        edit.n += 1
        return await client.receive_json()

    edit.n = 1
    made = await edit(op="save", name="Escape the flat", setting="Locked in", rules="Be kind")
    assert made["success"] and made["result"]["games"][0]["setting"] == "Locked in"
    gid = made["result"]["games"][0]["id"]
    await edit(op="set", game=gid, kind="item", key="torch", value="dim")
    listed = (await edit(op="set", game=gid, kind="clue", key="note under the rug", value="3-1-4"))["result"]["games"][0]
    torch = next(e for e in listed["entries"] if e["key"] == "torch")
    renamed = (await edit(op="set", game=gid, kind="item", key="torch", value="bright", entry=torch["id"]))["result"]["games"][0]
    assert [e["value"] for e in renamed["entries"] if e["key"] == "torch"] == ["bright"] and len(renamed["entries"]) == 2
    assert (await edit(op="start", game=gid))["result"]["games"][0]["active"] is True
    cleared = (await edit(op="remove", game=gid, entry=torch["id"]))["result"]["games"][0]
    assert [e["key"] for e in cleared["entries"]] == ["note under the rug"]
    assert (await edit(op="restart", game=gid))["result"]["games"][0]["entries"] == []
    assert (await edit(op="stop"))["result"]["games"][0]["active"] is False
    assert (await edit(op="delete", game="nope"))["success"] is False
    assert (await edit(op="delete", game=gid))["result"]["games"] == []


async def test_the_game_service_starts_and_stops_a_game(hass: HomeAssistant) -> None:
    entry = await _setup(hass, games=True)
    entry.runtime_data.games.save_game("Escape the flat")
    out = await hass.services.async_call(DOMAIN, "game", {"action": "start", "game": "escape"}, blocking=True, return_response=True)
    assert out == {"playing": "Escape the flat"} and entry.runtime_data.games.active is not None
    out = await hass.services.async_call(DOMAIN, "game", {"action": "stop"}, blocking=True, return_response=True)
    assert out == {"stopped": "Escape the flat"} and entry.runtime_data.games.active is None


# ----- ready-made templates: any house fills in its own names and rooms ----------------------------------
from homeassistant.helpers import area_registry as ar  # noqa: E402

from custom_components.sentient import game_templates  # noqa: E402


async def test_a_template_makes_an_ordinary_game_from_the_houses_own_names(hass: HomeAssistant) -> None:
    entry = await _setup(hass, games=True)
    for name in ("Study", "Living room", "Cellar"):
        ar.async_get(hass).async_create(name)
    games = entry.runtime_data.games
    defaults = games.template_defaults()
    assert defaults["master"] == "Nova" and defaults["rooms"] == "Cellar, Living room, Study" and defaults["start_room"] == "Living room"
    game = games.create_from_template("escape-the-house", {"title": "Escape the cellar", "players": "Ann, Bo and Cy", "helpers": "Dee (blunt hints)"})
    assert game["name"] == "Escape the cellar"
    assert "Nova is the game master" in game["rules"]
    assert "Cellar, Living room and Study" in game["setting"] and "Ann, Bo and Cy" in game["setting"] and "Dee (blunt hints)" in game["setting"]
    state = {e["key"]: e["value"] for e in game["entries"]}
    assert state["where"] == "Living room" and state["cores restored"] == "0 of 3" and state["front door"] == "locked"
    assert state["core: Study"] == "locked"
    games.save_game("Escape the cellar", "Edited", game["rules"], game["id"])  # an ordinary game afterwards
    assert games.games[game["id"]]["setting"] == "Edited"


def test_no_house_specific_name_is_built_in() -> None:
    built = game_templates.build("escape-the-house", {"rooms": "Hall, Attic"}, {})
    text = " ".join([built["name"], built["setting"], built["rules"]])
    for name in ("Kendra", "Riley", "Nia", "Sydra", "Vybes", "Pops", "Kyra"):
        assert name not in text


def test_the_text_always_fits_the_game_limits_and_grown_up_mode_drops_the_kid_lines() -> None:
    longest = {k: "x" * 500 for k in ("title", "master", "house_ai", "players", "helpers", "start_room")}
    built = game_templates.build("escape-the-house", {**longest, "rooms": ", ".join(f"Room number {i}" for i in range(40))}, {})
    assert len(built["setting"]) <= 1200 and len(built["rules"]) <= 1200
    assert len([e for e in built["entries"] if e[1].startswith("core: ")]) == game_templates.MAX_ROOMS
    adult = game_templates.build("escape-the-house", {"rooms": "Hall", "kids": "no"}, {})
    assert "kid-friendly" not in adult["rules"]
    with pytest.raises(ValueError):
        game_templates.build("escape-the-house", {}, {})
    with pytest.raises(ValueError):
        game_templates.build("nope", {"rooms": "Hall"}, {})


async def test_the_page_and_the_voice_can_start_from_a_template(hass: HomeAssistant, hass_ws_client) -> None:
    await _setup(hass, games=True)
    ar.async_get(hass).async_create("Kitchen")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sentient/games"})
    listing = (await client.receive_json())["result"]
    tpl = listing["templates"][0]
    assert tpl["id"] == "escape-the-house" and {f["key"]: f["default"] for f in tpl["fields"]}["rooms"] == "Kitchen"
    await client.send_json({"id": 2, "type": "sentient/games/edit", "op": "from_template", "template": "escape-the-house",
                            "values": {"title": "Flat escape", "rooms": "Kitchen, Hall"}})
    result = await client.receive_json()
    assert result["success"] and [g["name"] for g in result["result"]["games"]] == ["Flat escape"]
    await client.send_json({"id": 3, "type": "sentient/games/edit", "op": "from_template", "template": "escape-the-house", "values": {"rooms": ""}})
    assert (await client.receive_json())["success"] is True  # falls back to the house's own areas
    api = await _api(hass)
    made = await _call(api, "sentient_game", action="template", game="Voice escape", rooms="Loft, Garage", players="Sam")
    assert made.get("created_and_started") == "Voice escape"
    api = await _api(hass)
    assert "Loft and Garage" in api.api_prompt and "core: Loft: locked" in api.api_prompt


async def test_templates_are_hidden_when_story_games_are_off(hass: HomeAssistant, hass_ws_client) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sentient/games"})
    assert "templates" not in (await client.receive_json())["result"]
    await client.send_json({"id": 2, "type": "sentient/games/edit", "op": "from_template", "template": "escape-the-house", "values": {"rooms": "Hall"}})
    assert (await client.receive_json())["success"] is False
