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
- [ ] Dashboard card (core, feed and stats) shipped with the integration
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
