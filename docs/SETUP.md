# Setting up Sentient

Sentient works out of the box with just speakers and a conversation agent. Everything below is
optional and adds a feature; skip what you don't need.

## 1. The basics

1. Install through HACS (see the README) and restart Home Assistant.
2. *Settings → Devices & services → Add integration → Sentient.* Give it a name — that's the
   persona every voice uses and the name on the sidebar page. Pick your speakers (Assist
   satellites) and a default one, and set quiet hours.
3. In each voice assistant: *Settings → Voice assistants →* the assistant → *Control Home
   Assistant* → tick **Sentient** as well as **Assist**. That's what gives it the shared memory,
   house rules and tools.
4. Write your **house rules** (Settings tab → Voice): plain sentences every voice must follow, such
   as "Never say the house is empty".

Everything else lives on Sentient's own **Settings** tab on its sidebar page, grouped into menus.

## 2. An AI Task entity (camera and background jobs)

Doorbell visitors, camera watches and checks, the drive, the daily digest, briefings in words and
the overnight reflection use Home Assistant's **AI Task** — add one from your AI integration
(Google Gemini, OpenAI, Ollama…) and pick it under *Cameras & video → Doorbell & AI looks*. The
*daily AI limit* there caps background looks; things people ask for always go through.

## 3. Rooms

Sentient speaks where people are, so it needs to know which room things are in:

- Put each speaker and each motion or presence sensor in an **area**.
- Hallways and landings can be marked *pass-through* (Voice & sound → Quiet & rooms) so someone
  walking past doesn't pull the message there.
- Speakers you only want used when nobody's in any other room go in *backup speakers*.

## 4. Dashboard cards

Both cards load with the integration — no resources to add.

```yaml
type: custom:sentient-hub-card
height: 688                     # optional: a fixed height; the feed fills what's left
feed_rows: 6
talk_script: script.tap_to_talk # optional: what the big button runs (default: Assist, listening)
nodes:                          # optional: speakers to show (default: every Assist satellite)
  - assist_satellite.kitchen
  - assist_satellite.living_room
system:                         # optional: vitals bars
  cpu: sensor.system_monitor_processor_use
  memory: sensor.system_monitor_memory_usage
  disk: sensor.system_monitor_disk_usage
```

```yaml
type: custom:sentient-shows-card   # light-show buttons; hides itself when none are chosen
```

## 5. Camera spots

On the sidebar page, *Settings → Cameras & video → Spots*: pick a camera and either let the AI
find a named spot ("the rabbit pen") or draw it yourself. Watches, looks and checks that mention
a spot only send that part of the picture — sharper answers for less.

**The drive:** draw a spot called **drive** on the camera that sees it. Sentient then takes one
look when the camera's motion or car sensors settle down. If the camera's own sensors are too
busy (trees, the road), choose the sensors yourself under *Cameras & video → The drive* — a
Frigate zone's car or occupancy sensor is ideal.

## 6. Room loudness (ESPHome)

A voice speaker's microphone can tell Sentient how loud its room is. That lets light shows follow
the music and lets Sentient speak up over a TV. Add this to an ESPHome voice speaker that already
has a `microphone:` (it reports only while the switch is on, so it doesn't flood the database):

```yaml
sensor:
  - platform: sound_level
    microphone: mic               # your microphone's id
    passive: true                 # share the mic with the wake word; measures while it listens
    measurement_duration: 200ms
    peak:
      name: Room loudness
      filters:
        - lambda: |-
            if (!id(report_loudness).state) return {};
            return x;
        - delta: 1.5

switch:
  - platform: template
    id: report_loudness
    name: Report room loudness
    icon: mdi:waveform
    optimistic: true
    restore_mode: ALWAYS_OFF
    entity_category: config
```

Sentient finds the sensor and switch on the same device as the speaker by themselves. For light
shows, pick the sensor under *Home & routines → Light shows*.

## 7. The mind

On by default, all under *Brain & system → Mind*:

- **Remember conversations** — a short record of what was asked and said on every speaker, kept
  two weeks.
- **Check back on things** — follow-ups voices set, and watches that run out closed off out loud.
- **Curiosity** — at most one question a day, answers saved as memories.
- **Overnight reflection** — one AI call a night (time is a setting); new memories, morning
  thoughts and a question.

## 8. Phones

*Voice & sound → Phone notifications*: pick notify entities (e.g. the Home Assistant app) and
what goes to them.

## Troubleshooting

- **A card doesn't appear after an update:** the browser is holding the old version — refresh
  the page (on a wall tablet, reload the app or clear its cache).
- **Nothing is said:** check the *Last spoken* sensor's log — every message held back says why
  (quiet hours, hush, duplicate, busy…), and so does the Timeline tab.
- **Diagnostics:** *Settings → Devices & services → Sentient → ⋮ → Download diagnostics.*
