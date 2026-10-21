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
  - speak where people actually are. Each room gets an activity score from the last few minutes of motion, with a big boost when someone has just spoken to a speaker there. Hallways and landings can be marked as pass-through so a passer-by doesn't steal the message. By default every occupied room hears it. Speakers and sensors are matched by area, and the history is rebuilt from the recorder after a restart.
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
