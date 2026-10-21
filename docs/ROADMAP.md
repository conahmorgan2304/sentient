# Roadmap

Sentient is built in stages. Each stage is used in a real house before the next one starts.

## Stage 1 — Shared brain *(in progress)*
- [x] Setup wizard: name, speakers, quiet hours, banned words, hush-after automations
- [x] Long-term memory with merging of near-duplicates, and services
- [x] LLM API: memories in the prompt, plus remember / recall / forget / announce tools
- [x] Speech router: quiet hours, word filter, duplicates, hush periods, room-following speaker choice
- [x] Room awareness for the person talking
- [x] Activity-scored rooms: decaying motion history, voice-interaction boost, pass-through areas, every-occupied-room mode, seeded from the recorder (0.2.0)
- [x] Memory and last-spoken sensors
- [ ] Dashboard card (core, feed and stats) shipped with the integration
- [x] Memory manager in the options flow (view, edit, delete) (0.3.0)

## Stage 2 — Watches *(in progress)*
- [x] "Keep an eye on X until Y and tell me if Z", by voice (0.4.0)
- [x] Device/sensor watches: reach a state, or cross a number — no AI needed (0.4.0)
- [x] Camera checks through the configured AI task, woken only by that camera's own motion/object sensors, with a cooldown (0.4.0)
- [ ] Crop camera checks to a zone rather than the whole frame
- [x] Local times everywhere; never read IDs aloud; list and cancel by voice (0.4.0)
- [ ] Doorbell / visitor / delivery awareness built on Frigate zones

## Stage 3 — Speaking up
- [ ] Event triage: free rules first, then the cheapest model, and a bigger model only when needed
- [ ] Daily AI spend cap
- [ ] Proactive remarks and questions with follow-up listening, e.g. "Fancy a brew?"
- [ ] Morning and evening briefings

## Stage 4 — Learning
- [ ] Learn routines from history ("you usually ... around this time")
- [ ] Suggest automations from repeated manual actions
- [ ] Weekly memory consolidation
