# Sentient

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
- **Watches.** "Tell me when the washing machine finishes", "tell me if the bedroom goes above 24", "watch the garden until 6 and tell me if the cats go near the rabbit pen". Device watches are free; camera watches only ask your AI when that camera's own motion or object sensor fires (or a zone sensor you name), at most once a minute and up to a daily limit you set. They survive restarts, expire on their own, and can be listed or cancelled by voice. Services: `sentient.watch`, `sentient.cancel_watch`.
- **Noticing.** Pick the things worth noticing (washing machine, windows, weather, vacuum...). When they change, free rules run first — quiet hours, hush periods, an hourly limit, ignoring jitter, batching changes together — and only then does your AI decide whether it's worth a remark, or a question that opens the mic ("It's started raining and the bedroom window's open — want me to remind you in ten minutes?").
- **House rules.** Plain sentences every voice must follow ("Never say the house is empty").
- **Ask.** `sentient.ask` asks out loud in the room people are in and listens for the answer — handy for automations like a brew offer.
- **Reminders.** "Remind me at 6 to put the oven on" — said at that time in whichever room people are in (it gets through quiet hours, because someone asked for it). Service: `sentient.remind`.
- **Look.** "What's going on in the garden?", "is there a parcel at the front door?" — one on-demand look through a camera via your AI task.
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
