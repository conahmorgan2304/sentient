# Roadmap

Sentient is built in stages. Each stage is used in a real house before the next one starts.

## Stage 1 — Shared brain *(in progress)*
- [x] Setup wizard: name, speakers, quiet hours, banned words, hush-after automations
- [x] Long-term memory with merging of near-duplicates, and services
- [x] LLM API: memories in the prompt, plus remember / recall / forget / announce tools
- [x] Speech router: quiet hours, word filter, duplicates, hush periods, room-following speaker choice
- [x] Room awareness for the person talking
- [x] Backup-only speakers, used only when no main speaker's room is occupied (0.5.0)
- [x] Activity-scored rooms: decaying motion history, voice-interaction boost, pass-through areas, every-occupied-room mode, seeded from the recorder (0.2.0)
- [x] Memory and last-spoken sensors
- [x] Dashboard card (core, feed and stats) shipped with the integration (0.40.0: custom:sentient-hub-card, custom:sentient-shows-card)
- [x] Memory manager in the options flow (view, edit, delete) (0.3.0)

## Stage 2 — Watches *(in progress)*
- [x] "Keep an eye on X until Y and tell me if Z", by voice (0.4.0)
- [x] Device/sensor watches: reach a state, or cross a number — no AI needed (0.4.0)
- [x] Camera checks through the configured AI task, woken only by that camera's own motion/object sensors, with a cooldown (0.4.0)
- [x] On-demand camera questions by voice (`sentient_look`) (0.4.1)
- [x] Wake a camera watch on a zone or object sensor (e.g. a Frigate zone) instead of the whole camera (0.5.0)
- [x] Spoken reminders at a time, in the room people are in (0.5.0)
- [x] Spots: teach a named area by voice; watches and looks that mention it send only that crop (0.10.0)
- [x] Local times everywhere; never read IDs aloud; list and cancel by voice (0.4.0)
- [x] Doorbell visitor log: one AI look when the bell rings, kept quiet by default; parcel-left check after deliveries (0.9.0)

## Stage 3 — Speaking up *(in progress)*
- [x] Noticing: chosen entities → free rules (quiet hours, hush, hourly limit, jitter, batching) → AI decides whether to speak (0.6.0)
- [x] House rules every voice must follow (0.6.0)
- [x] `sentient.ask`: ask out loud and open the mic for the reply (0.6.0)
- [x] A bigger model only when the cheap one is unsure: optional second-opinion AI for camera watches (0.17.0)
- [x] Daily limit on background camera checks (0.5.0)
- [x] Proactive remarks and questions with follow-up listening (0.6.0)
- [x] Briefings on request: facts for any voice agent, or spoken via `sentient.briefing` (0.8.0)

## Stage 4 — Learning *(in progress)*
- [x] Learn routines from history with plain statistics — no AI (0.7.0)
- [x] Share them with every voice agent, offer each once, and do adopted ones at their usual time (0.7.0)
- [x] Learn links — what people do straight after something else ("when the back door opens after dark, the kitchen light goes on"), offered and done on request (0.32.0)
- [x] Delete learned routines and habits for good (✕ on the page, by voice or `sentient.forget_routine`), with a bring-back (0.33.0)
- [x] Learning from feedback: shushes, hushes straight after a remark and declined routines make it quieter about those things by itself, fading over a month (0.34.0)
- [x] Weekly report: what it learned and noticed this week (0.34.0)
- [x] Weekly memory tidy: merge duplicates, drop nonsense, never more than a third at once (0.8.0)

## Stage 5 — Its own page
- [x] Built-in sidebar panel, not an editable dashboard (0.13.0)
- [x] House health: offline devices, low batteries, updates (0.14.0)
- [x] Settings tab in the panel: every option plus a memory manager (0.15.0)
- [x] Camera spots page: AI find or draw by hand, outlines on the live picture, ask a camera (0.16.0)
- [x] Set reminders from the page (0.17.0)
- [x] Motion lights: after dark, quiet hours per light, late-night dimming, hands off manual use (0.18.0)
- [x] Motion lights: optional lux sensor, voice tool, live overview card (0.19.0)
- [x] Timeline tab and voice tool: what the house did and noticed (0.20.0)
- [x] Phone notifications for chosen kinds of timeline entries (0.21.0)
- [x] Hush and guest mode: switches, service, voice tool, page buttons (0.22.0)
- [x] Chime on/off: setting, switch and page button (0.23.0)
- [x] Daily digest; timeline keeps health baseline across restarts and names rooms (0.24.0)
- [x] Welcome home with what happened while you were out (0.25.0)
- [x] Speaking volume and media ducking, shush, automatic safety alerts (0.26.0)
- [x] Named routines (goodnight, morning…) and backup/restore (0.27.0)
- [x] Energy: today vs usual from Energy-dashboard devices, card, briefing, voice tool, unusual-use notes (0.28.0)
- [x] Energy on/off and cost; routine buttons on the overview (0.28.1)
- [x] Security check (tool, card, briefing, routine check) and scenes by description (0.29.0)
- [x] Left-open nudges and a leaving check (0.30.0)
- [x] Safety alerts: alarm triggered, tamper, extra entities, test button (0.30.1)
- [x] Parcels left without a ring: one look after a doorstep visit ends, said only when something new appears (0.31.0)
- [x] Heating that learns: warm-up rate, usual temperature, getting-up times; optional warm-up before people are up, back on the thermostat's schedule afterwards (0.34.0)
- [x] Night watch: doors and gates opening at times they never normally do with nobody up inside — alert, optionally out loud through quiet hours (0.34.0)
- [x] Fixes from a week of real use: night watch only in quiet hours and knows the house is up (door then gate is one trip), motion lights ignore lights reporting back, house health ignores entities that never have a reading, phone layout, says so when a voice request didn't happen, heating turned up goes back after a set time, no offers it can't carry out (0.35.0)
- [x] Personality setting used everywhere; offers (brew, heating…) with their own page; announcements (doorbell, gate) through quiet hours with a 'someone going out' skip; alarms with routines, room follow-ups, calendar ('bins tonight: recycling') and voice control; standing camera checks (is there a delivery van?); camera security — routes across cameras, over the fence, anyone about at night; spots with other names and drawn shapes that pick the right camera (0.36.0)
- [x] Settings grouped into menus that fold away: voice & sound, cameras & video, security & safety, home & routines, brain & system (0.37.0)
- [x] Disco mode: disco, party, rainbow and chill light shows for chosen lights or a named room; lights go back exactly as they were (even after a restart); switch, voice and an action (0.38.0)
- [x] Light shows: whole house ('everywhere'), lights to leave out, Halloween and Christmas styles, following the music from a loudness sensor, and a choice of which shows get an overview button (0.39.0)
- [x] Quieter: no remarks about something a person just switched on; voice follow-through only after real speech; shorter camera journeys; hub and light-show dashboard cards (0.40.0)
- [x] Feeling more alive: speaks up in a noisy room, messages follow people to the next room, follow-ups and closed-off watches, conversations remembered across speakers, daily curiosity, overnight reflection with morning thoughts, cars on the drive (0.41.0)
- [x] Ready to share: setup guide, screenshots, brand images checked by the HACS action (0.41.0)
- [x] Auto-off (on too long, or the room empty) and silent schedule alarms, so a house's everyday automations can live in Sentient (0.42.0)
- [x] Echos and other media players as speakers, with an on/off setting and switch; routines that take details (rooms, times) (0.43.1)
- [x] Mind card on the sidebar page, 'why did that happen?' for any voice, and an evening look at tomorrow (0.44.0)
- [x] Asleep/awake for the whole house with bedtime and morning actions; auto-off learns from lights switched straight back on (0.45.0)
- [x] AI meter and daily cap with plain-words fallback, self-check of its own parts, learning from corrections, appliances that say when they've finished or seem stuck; overview fits the screen on tablets (0.46.0)
- [x] Voice backup agent (AI first, Home Assistant's own when the AI fails); animals' hutch/kennel temperatures with frost warnings (0.47.0)
- [x] Set-up wizard filled in from the house; thumbs up/down on remarks (0.48.0)
- [x] One-call house check, voice action log and 'why didn't you say', watchdog for wear, fighting automations and frozen cameras (0.49.0)
- [x] Tablet screen card: alerts with the camera, notices, morning card, idle glance; on/off setting and switch (0.50.0)
- [x] The drive compares pictures before asking the AI; tools never crash a voice reply (0.51.0)
- [x] Echos kept for alarms, reminders and safety alerts only; camera security quiet when the AI sees nobody (0.52.0)
- [x] Away lights: last week replayed while everyone's out after dark, stops when anyone's home (0.53.0)
