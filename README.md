# Sentient

<p align="center"><img src="docs/images/sentient.jpg" alt="Sentient" width="420"></p>

**An evolving AI brain for Home Assistant.**

Sentient gives your home one shared brain. Every voice assistant and automation in the house uses the same memory, follows the same house rules about when and where to speak, and gets smarter the longer it runs.

It works with the AI you already have in Home Assistant (Google Gemini, OpenAI, Ollama, anything with a conversation agent). It doesn't need its own API key.

> Status: early development (v0.1). Stage 1 of the roadmap is in.

## What it does today

- **Shared long-term memory.** "Remember the rabbits' hutch is by the shed." Any voice in the house can then recall it. Near-duplicates are merged rather than piling up.
- **One brain for every voice.** Tick **Sentient** under *Control Home Assistant* in any conversation agent. That agent gets the household's memories in its context, plus tools to remember, recall, forget and announce.
- **House speech rules, in one place.** `sentient.say` and the voice tools:
  - respect quiet hours, which urgent messages still break through;
  - strip words you never want used ("sir", ...);
  - drop duplicates;
  - stay quiet for a set time after chosen automations, e.g. your doorbell flow;
  - speak where people actually are. Each room gets an activity score from the last few minutes of motion, with a big boost when someone has just spoken to a speaker there. Hallways and landings can be marked as pass-through so a passer-by doesn't steal the message. By default every occupied room hears it; speakers you mark backup-only are used only when nobody is in any other speaker's room. Speakers and sensors are matched by area, and the history is rebuilt from the recorder after a restart.
- **Watches.** "Tell me when the washing machine finishes", "tell me if the bedroom goes above 24", "watch the garden until 6 and tell me if the cats go near the rabbit pen". Device watches are free; camera watches only ask your AI when that camera's own motion or object sensor fires (or a zone sensor you name), at most once a minute and up to a daily limit you set. If the AI says the picture is too dark or unclear to be sure, an optional bigger "second opinion" model is asked — only then. They survive restarts, expire on their own, and can be listed or cancelled by voice. Services: `sentient.watch`, `sentient.cancel_watch`.
- **Noticing.** Pick the things worth noticing (washing machine, windows, weather, vacuum...). When they change, free rules run first — quiet hours, hush periods, an hourly limit, ignoring jitter, batching changes together — and only then does your AI decide whether it's worth a remark, or a question that opens the mic ("It's started raining and the bedroom window's open — want me to remind you in ten minutes?").
- **House rules.** Plain sentences every voice must follow ("Never say the house is empty").
- **Ask.** `sentient.ask` asks out loud in the room people are in and listens for the answer — handy for automations like a brew offer.
- **Learning.** Every night Sentient looks at two weeks of history for the lights and switches you choose and spots routines ("the hallway light usually goes on around 5:30pm on weekdays") with plain statistics — no AI, and anything an automation already does to the minute is ignored. Voice assistants know your routines, and once per new routine, during the day when someone's home, it offers: "want me to do that for you from now on?". Say yes and it does.
- **Visitors.** When the doorbell rings, one AI look through the door camera logs who came ("a delivery driver holding a small parcel") — quietly, unless you choose to have it announced. After a delivery it looks again and can say a parcel's been left. Voice assistants answer "did anyone come round?" from the log.
- **Briefings.** "What's my briefing?" — you choose what's in it (time, weather, reminders, watches, routines coming up, anything open, visitors, recent remarks, system health and updates, to-do lists, calendars) on its own settings page, can turn briefings off, and can set a daily time (only spoken when someone's home and outside quiet hours). `sentient.briefing` speaks one where people are.
- **Memory upkeep.** Every Sunday night the AI merges duplicate memories and drops nonsense, never touching more than a third at once (`sentient.tidy_memories` to run it now).
- **Reminders.** "Remind me at 6 to put the oven on" — said at that time in whichever room people are in (it gets through quiet hours, because someone asked for it). Service: `sentient.remind`.
- **Spots.** "On the garden camera, the rabbit pen is a spot" — the AI finds it once and remembers where it is. Watches and looks that mention it then check only that part of the picture: sharper answers for less.
- **Look.** "What's going on in the garden?", "is there a parcel at the front door?" — one on-demand look through a camera via your AI task.
- **Motion lights.** Pick a light and its motion sensors on the Settings tab: it comes on with movement between sunset and sunrise and goes off a few minutes after it stops. Each light chooses whether quiet hours keep it off (a landing might not want that), its brightness, and a dimmer brightness for late at night (a window you set, 23:00–06:00 by default). A light someone switched on by hand is left alone, and if someone changes one the house switched on, it's theirs until it's next off. For a dim room, add a light sensor and "dark" means below a lux level instead of after sunset. Voice assistants can list them or stop one ("stop the landing light coming on with motion"), and the overview shows each one live with an on/off switch. No AI, and routines aren't learned from these lights.
- **Timeline.** One running log of what the house did and noticed — what it said (and where), what it held back and why, remarks it decided against, doorbell visitors, watches and reminders going off, motion lights, devices going offline and coming back. A Timeline tab on the sidebar page shows the last three days live, with filters, and voice assistants can answer "what happened while I was out?". A **daily digest** (on demand from the Timeline tab, or at a time you set) has your AI sum the day up in a few sentences — kept on the timeline and optionally sent to phones.
- **Phone notifications.** Pick the phones (any notify entity, e.g. the Home Assistant app) and what goes to them: urgent announcements, things held back in quiet hours, things nobody could hear, doorbell visitors and parcels, watches and reminders, house health problems. Each message at most once in ten minutes.
- **Welcome home.** Optional: when someone gets back after 20 minutes or more, a "Welcome back" in the room they're in — with a sentence or two on what's worth knowing from while they were out (visitors, things that finished, problems), written by your AI from the timeline. Never in quiet hours, hush or guest mode, no names, once for people arriving together.
- **Speaking to suit the room.** Optional speaking volume (and a quieter one for quiet hours), set on the speaker's own volume control and put back afterwards; anything playing in the same room is turned down while Sentient talks, then back up.
- **Shush.** "Stop telling me about the washing machine" (or "…for tonight") — that thing isn't noticed or mentioned until the time's up or it's unshushed; a Shushed page lists them. Urgent and safety alerts always get through.
- **Safety alerts.** Smoke, carbon monoxide, gas and water-leak sensors are found automatically: said on every speaker through quiet hours, hush and guest mode, repeated twice while still on, logged and sent to phones.
- **Routines.** Named routines — "goodnight", "good morning", "leaving" — built with Home Assistant's own action editor on the Settings page, with other ways of asking, an optional line to say, and optional checks first ("the back door is still open, the landing light is still on"). Run by voice, a button on the sidebar page or `sentient.run_routine`.
- **Backup.** Download everything Sentient has learned and been told as one file, and restore it (adding, never deleting) from the Settings page.
- **Energy.** Uses the devices already on Home Assistant's Energy dashboard: today's use compared with the same part of the day over the past week, an Energy card on the sidebar page, an optional briefing section, a voice tool ("what's using all the electricity?") and a timeline note when something uses far more than usual. Plain statistics, no AI. Can be switched off, and an optional unit rate shows today's cost.
- **Chime.** Choose whether speakers chime before Sentient talks — a setting, a `Chime` switch for automations (e.g. off at night) and a button on the sidebar page.
- **Hush and guest mode.** "Be quiet for an hour" (or the Hush switch, or `sentient.hush`) stops everything but urgent messages until the time's up. Guest mode stops remarks and routine offers and keeps personal household details out of answers. Both are switches, voice tools and buttons on the sidebar page; motion lights carry on as normal.
- **House health.** A local check every five minutes — no AI — for devices that have gone offline (every main entity unavailable, or the device's own connectivity sensor saying it's lost its connection — reported per device, after ten minutes), batteries below your threshold (one per device) and pending updates. Shown on the sidebar page, in briefings and to voice assistants; an ignore list keeps things that are meant to be off out of it.
- **Its own sidebar page.** A built-in panel (named after your persona) shows house health, learned routines (tap to let the house do one), watches and reminders (✕ to cancel, plus a quick reminder box: "20m", "1h" or "18:30"), visitors, what was said or held back and why, and where people are — plus a briefing button and an Ask box. A **Settings** tab holds every option (the same pickers as the integration's settings, grouped: voice, quiet hours and rooms, briefings, noticing and learning, door and cameras, house health, this page), a full memory manager — search, add, edit in place and forget — and **Camera spots**: pick a camera, let the AI find a named spot or drag a box on the picture yourself, see every spot outlined, and ask the camera a question about the whole picture or one spot. It's part of the integration, so it isn't an editable dashboard; turn it off in the settings if you don't want it.
- **Memory manager.** Settings → Sentient → Configure → *Memories*: see everything the house remembers, edit, forget or add by hand.
- **Room awareness.** The voice assistant is told which room the person talking to it is in, and a *Likely room* sensor shows where Sentient thinks people are (with every room's score).
- **Sensors.** `sensor.<name>_memories` (with the list) and `sensor.<name>_last_spoken` (with a log of what was said or held back and why).

## Install (HACS, custom repository)

1. HACS → ⋮ → *Custom repositories* → add this repo's URL as an **Integration**.
2. Install **Sentient**, then restart Home Assistant.
3. *Settings → Devices & services → Add integration → Sentient*. Give it a name, pick your speakers and set quiet hours.
4. *Settings → Voice assistants →* your agent → *Control Home Assistant* → tick **Sentient** as well as **Assist**.

## Services

| Service | What it does |
|---|---|
| `sentient.say` | Speak through the house. `priority`: `low` never interrupts, `urgent` ignores quiet hours. `speaker`: a room name, speaker name or entity id; leave empty to follow the person. Returns what happened. |
| `sentient.remember` | Add a memory. |
| `sentient.forget` | Remove a memory by id or by matching text. |

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).
