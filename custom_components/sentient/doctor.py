"""Setup doctor: one page that checks Sentient's setup and says what to fix.

Read-only checks, no AI calls: the AI agents, the voice pipeline, every speaker, text-to-speech and the
reading voice, the cameras Sentient uses, the Alexa skill, and the folders for books, recipes and manuals.
Each result is ok / warn / bad with one plain line on what to do. Speaking a test line on each speaker is a
separate button, since that makes a noise.
"""

from __future__ import annotations

import os
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ALEXA_SKILL,
    CONF_BOOKS,
    CONF_CLIP_CAMERAS,
    CONF_CLIP_WATCH,
    CONF_DEFAULT_SPEAKER,
    CONF_DOOR_CAMERA,
    CONF_DRIVE_CAMERA,
    CONF_FACE_TEACH_CAMERAS,
    CONF_READING_TTS,
    CONF_RECIPES,
    CONF_SPEAKERS,
    CONF_TTS_ENTITY,
    DOMAIN,
)

DOWN = ("unavailable", "unknown")


def _row(area: str, name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"area": area, "name": name, "status": status, "detail": detail, "fix": fix}


def _name(hass: HomeAssistant, entity_id: str) -> str:
    st = hass.states.get(entity_id)
    return st.name if st else entity_id


def _llm_apis(hass: HomeAssistant, agent: str) -> list[str] | None:
    """The 'Control Home Assistant' choices of a conversation agent, when they can be read."""
    entry = er.async_get(hass).async_get(agent)
    if entry is None or not entry.config_entry_id:
        return None
    cfg = hass.config_entries.async_get_entry(entry.config_entry_id)
    if cfg is None:
        return None
    sub = cfg.subentries.get(entry.config_subentry_id) if getattr(entry, "config_subentry_id", None) else None
    apis = (sub.data if sub else cfg.options).get("llm_hass_api")
    if apis is None:
        return None
    return [apis] if isinstance(apis, str) else list(apis)


def folders(rt: Any) -> dict[str, str | None]:
    opts = rt.options
    return {
        "Books folder": rt.books.folder() if opts.get(CONF_BOOKS) and getattr(rt, "books", None) else None,
        "Recipes folder": rt.recipes._folder()[0] if opts.get(CONF_RECIPES) and getattr(rt, "recipes", None) else None,
    }


def check(hass: HomeAssistant, rt: Any, found: dict[str, bool] | None = None) -> list[dict[str, str]]:
    from .conversation import find_ai_agent

    opts = rt.options
    out: list[dict[str, str]] = []

    # --- the AI ------------------------------------------------------------------------------
    agent = find_ai_agent(hass, opts, None)
    if agent is None:
        out.append(_row("AI", "Voice AI agent", "bad", "No AI conversation agent found for Sentient to use.",
                        "Settings → Voice & sound → choose your AI agent (or Settings → This page → Ask box agent)."))
    elif (st := hass.states.get(agent)) is None or st.state in DOWN:
        out.append(_row("AI", "Voice AI agent", "bad", f"{_name(hass, agent)} isn't available.", "Check that integration in Settings → Devices & services."))
    else:
        apis = _llm_apis(hass, agent)
        if apis is not None and "sentient" not in apis and "sentient_guarded" not in apis:
            out.append(_row("AI", "Voice AI agent", "warn", f"{_name(hass, agent)} doesn't have Sentient ticked, so it can't use the house's memory or tools.",
                            "Settings → Voice assistants → that agent → Control Home Assistant → tick Sentient."))
        else:
            out.append(_row("AI", "Voice AI agent", "ok", f"{_name(hass, agent)}" + (" with Sentient ticked." if apis else ".")))
    if getattr(rt.ai, "failing", False):
        out.append(_row("AI", "AI answering", "bad", f"The AI isn't answering: {(rt.ai.last_error or '')[:120]}",
                        "Check the AI service (quota, key, internet). Simple things still work through the backup agent."))
    task = opts.get(CONF_AI_TASK_ENTITY)
    if task and ((st := hass.states.get(task)) is None or st.state in DOWN):
        out.append(_row("AI", "AI task entity", "bad", f"{task} isn't available — camera looks, briefings and summaries need it.",
                        "Settings → Brain → choose a working AI task entity."))
    elif not task and not hass.states.async_entity_ids("ai_task"):
        out.append(_row("AI", "AI task entity", "warn", "No AI task entity — cameras, briefings and summaries can't use the AI.",
                        "Add one with your AI integration (Google, OpenAI…), then pick it in Settings → Brain."))
    else:
        out.append(_row("AI", "AI task entity", "ok", _name(hass, task) if task else "Home Assistant's default."))

    # --- the voice pipeline ------------------------------------------------------------------
    try:
        from homeassistant.components.assist_pipeline import async_get_pipeline

        engine = async_get_pipeline(hass).conversation_engine
    except Exception:  # noqa: BLE001 - no pipelines
        engine = None
    if engine:
        entry = er.async_get(hass).async_get(engine)
        ours = bool(entry and entry.platform == DOMAIN)
        if ours or engine == agent:
            out.append(_row("Voice", "Voice pipeline", "ok", f"Your preferred pipeline uses {_name(hass, engine)}."))
        else:
            out.append(_row("Voice", "Voice pipeline", "warn", f"Your preferred pipeline uses {_name(hass, engine)}, not Sentient's agents.",
                            "Settings → Voice assistants → your assistant → Conversation agent: '… with backup'."))

    # --- speakers ------------------------------------------------------------------------------
    speakers = list(opts.get(CONF_SPEAKERS) or [])
    if not speakers:
        out.append(_row("Speakers", "Speakers", "bad", "No speakers chosen, so Sentient can't say anything.", "Settings → Voice & sound → Speakers."))
    for sp in speakers:
        st = hass.states.get(sp)
        if st is None:
            out.append(_row("Speakers", sp, "bad", "This speaker doesn't exist any more.", "Remove it in Settings → Voice & sound → Speakers."))
        elif st.state in DOWN:
            out.append(_row("Speakers", st.name, "bad", "Offline.", "Check it's powered and connected."))
        elif sp.startswith("media_player.") and (er.async_get(hass).async_get(sp) or None) and \
                er.async_get(hass).async_get(sp).platform != "alexa_media" and not opts.get(CONF_TTS_ENTITY):
            out.append(_row("Speakers", st.name, "bad", "A media player needs a text-to-speech service to talk.",
                            "Settings → Voice & sound → Text-to-speech."))
        else:
            out.append(_row("Speakers", st.name, "ok", "Ready."))
    default = opts.get(CONF_DEFAULT_SPEAKER)
    if speakers and not default:
        out.append(_row("Speakers", "Default speaker", "warn", "No default speaker: when nobody's been seen, nothing is said.",
                        "Settings → Voice & sound → Default speaker."))
    if (tts := opts.get(CONF_TTS_ENTITY)) and hass.states.get(tts) is None:
        out.append(_row("Speakers", "Text-to-speech", "bad", f"{tts} doesn't exist.", "Choose another in Settings → Voice & sound."))
    if opts.get(CONF_BOOKS) and (voice := opts.get(CONF_READING_TTS)):
        recent = len(getattr(rt.speech, "reading_fallbacks", []) or [])
        if hass.states.get(voice) is None:
            out.append(_row("Speakers", "Reading voice", "bad", f"{voice} doesn't exist.", "Settings → Books & recipes → Reading voice."))
        elif recent:
            out.append(_row("Speakers", "Reading voice", "warn", f"It has failed {recent} time{'s' if recent > 1 else ''} recently and the usual voice read instead.",
                            "Try another voice with TRY THE VOICE; free voices can be slow."))
        else:
            out.append(_row("Speakers", "Reading voice", "ok", _name(hass, voice)))

    # --- cameras ---------------------------------------------------------------------------------
    cams = {opts.get(CONF_DOOR_CAMERA), opts.get(CONF_DRIVE_CAMERA), *(opts.get(CONF_FACE_TEACH_CAMERAS) or [])}
    if opts.get(CONF_CLIP_WATCH):
        cams |= set(opts.get(CONF_CLIP_CAMERAS) or [])
    for cam in sorted(c for c in cams if c):
        st = hass.states.get(cam)
        if st is None:
            out.append(_row("Cameras", cam, "bad", "This camera doesn't exist any more.", "Choose another in the camera settings."))
        elif st.state in DOWN:
            out.append(_row("Cameras", st.name, "warn", "Unavailable right now — asleep or offline.",
                            "If it's a battery camera, add a wake action under Sleepy cameras."))
        else:
            out.append(_row("Cameras", st.name, "ok", "Live."))

    # --- Alexa -----------------------------------------------------------------------------------
    if opts.get(CONF_ALEXA_SKILL):
        out.append(_row("Alexa", "Alexa skill", "ok" if agent else "bad",
                        "Questions through Alexa go to " + (_name(hass, agent) if agent else "no AI agent") + ".",
                        "" if agent else "Choose your AI agent in Settings, as above."))

    # --- folders (whether they exist was looked up off the event loop) ---------------------------
    for label, path in folders(rt).items():
        if path is None:
            continue
        here = (found or {}).get(label, False)
        out.append(_row("Folders", label, "ok" if here else "warn", "Found." if here else "Not there yet.",
                        "" if here else f"Create '{os.path.basename(path)}' in Home Assistant's media folder."))
    return out


async def async_check(hass: HomeAssistant, rt: Any) -> dict[str, Any]:
    paths = folders(rt)
    found = await hass.async_add_executor_job(lambda: {k: bool(p and os.path.isdir(p)) for k, p in paths.items()})
    rows = check(hass, rt, found)
    from . import voice_id

    if voice_id.enabled(hass, rt.options):  # nothing about voice recognition appears unless it is installed and switched on
        rows += [_row("Voice", t, st, msg, fix) if fix else _row("Voice", t, st, msg)
                 for t, st, msg, fix in voice_id.health_rows(await voice_id.details(hass))]
    worst = "bad" if any(r["status"] == "bad" for r in rows) else "warn" if any(r["status"] == "warn" for r in rows) else "ok"
    return {"status": worst, "rows": rows}
