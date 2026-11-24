"""What's new after an update: a few plain lines per release, shown on the Sentient page until seen.

Only releases newer than the one last marked as seen are shown, newest first. A fresh install starts as
"seen" (nothing to catch up on). Turning it off in Settings hides the card; the notes stay in the code.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .const import CONF_WHATS_NEW, DOMAIN

NOTES: dict[str, list[str]] = {
    "1.19.0": [
        "The Sentient page and cards follow your Home Assistant language: tabs, headings, buttons, editors and messages are translated into Polish, German, Dutch, Italian, Portuguese, Spanish, French and Hungarian. Dates and times use your language too. English stays as it was.",
        "The Settings forms, options, services and entity names are now fully translated into the same languages. Polish, German, Dutch, Italian and Portuguese are new, and the gaps in Spanish, French and Hungarian are filled.",
        "What Sentient writes itself (timeline entries, briefings, house health) is still in English for now; that comes next.",
    ],
    "1.18.1": [
        "\"Which room is my phone in?\" is answered from the room trackers again. The voice was going by the device list and the house context, which don't include trackers set up in Sentient, and said the phone wasn't showing.",
        "The nightly self-test no longer marks \"there's no smart lock I can use\" as a wrong answer.",
        "Camera watches work out what to look for from the request (\"watch for any more cars\" looks for a car), and a sensor named by mistake is swapped for the camera in the same area, instead of the voice retrying.",
        "Ready for Home Assistant 2027.5: the instant answers pass the turn's context when they join a conversation.",
    ],
    "1.18.0": [
        "Watches and reminders can act (Memory & learning): \"if the back door's open for ten minutes, turn the hall light off and tell me\", \"when the washing finishes, put the hallway light on\", \"at 11, if the back door's still open, tell me\". Read back first, set up only after a yes on the next turn, and each device is checked afterwards.",
        "Memory that understands time: \"for now\", \"at the moment\" and one-off things (\"I turned the heating on\") end by themselves, an old unconfirmed fact is checked once, and each day gets a short story (from the same overnight AI call) for \"what did we do last Saturday?\".",
        "Know who's where: voice recognition, phone room trackers and movement put together into one best guess per person at home, with how sure it is.",
        "Plans for big requests: \"get the house ready for film night\" is read back as a short plan, done after a yes, and each step checked.",
        "Grade its own answers: each night, what the voice said about doors, lights, switches and locks is checked against what the house recorded, and mistakes become lessons. No AI.",
        "All five are off until switched on in Settings → Memory & learning.",
        "Fixed for everyone: a habit named by its day (\"the bins go out on Thursday nights\") was given an end date and dropped after that Friday. It's kept now.",
    ],
    "1.17.2": [
        "\"Lock the front door\" in a house without a smart lock now gets the same plain answer every time, with no AI: \"There's no smart lock I can use, so that one needs doing by hand — the Front door is shut.\" It no longer says the house has no locks. (Part of Instant answers.)",
    ],
    "1.17.1": [
        "Situations now have a beginning and an end: someone arriving home, the heating struggling to reach its target (calling for heat an hour and hardly warming), a busy garden on the cameras, someone on the cameras, a device that keeps dropping offline, and the house asleep. Each carries when it started, what shows it, how sure it is and what ends it — and once it's over the voice is told it's over, so it doesn't bring up old news as if it's still happening. Settings › Memory & learning › Situations.",
        "Routines the house does for you: if one was already done by the time it was due on 5 days in two weeks (someone does it themselves now), Sentient asks once whether to keep it, and stops if you say no. Settings › Noticing & learning.",
        "Ready for tonight: what it's keeping an eye on now carries on after a restart (until its three hours are up).",
        "The voice prompt says a few rules once instead of two or three times. Nothing it can do has changed.",
    ],
    "1.17.0": [
        "New, off until switched on (Settings › Safety & security › Ready for tonight): \"make sure everything's ready for tonight\" checks the doors, windows and locks, switches off only the lights you've chosen (and checks they went off), looks at the heating, and says what's still open, unlocked, left on or can't be confirmed. Locks, the alarm and the heating are never changed, and the alarm is never offered. It can keep an eye on whatever was still open for up to three hours and say once, on the speaker you asked, when it's all sorted.",
        "Open-window heating now leaves a never-touch thermostat alone (the house is still told the window's open).",
        "How it's used now counts lists by voice, ready for tonight and open-window heating.",
    ],
    "1.16.8": [
        "Shopping and to-do lists by voice work properly (Settings › Voice › Shopping and to-do lists, on by default): \"mark milk as bought\", \"what's on the shopping list?\", \"take bin bags off\", \"put eggs back on\". Items are found however they're said — any case, plurals, the only milk on the list — two possible matches are asked about, adding something already there doesn't add it twice, and Sentient only says it's done once the list shows it. Your notes about lists from memory are followed.",
        "Room temperatures: a 3D printer's chamber, nozzle or spool holder (AMS), and fridge, freezer, oven, water-tank or battery temperatures, are no longer taken as the room's. Choosing the area's temperature sensor in Home Assistant still overrides everything.",
    ],
    "1.16.7": [
        "New, off until switched on (Settings › Heating › Turn a room's heater down while its window is open): when a window has been open for a few minutes and that room has its own heater, Sentient turns it down to the night temperature and puts it back when the window shuts — only if nobody has changed it meanwhile, and to what the heating schedule wants by then. A whole-house thermostat is never turned down for one window; Sentient just says once that the window's open and the heating's on. Nothing stays turned down for more than 3 hours.",
    ],
    "1.16.6": [
        "A heating offer when the heating is already on now asks to turn it up rather than to put it on — \"It's 18 degrees in here, and the heating's on at 18. Shall I turn it up to 20?\" — and a yes gets \"Okay, I'll turn it up to 20.\"",
    ],
    "1.16.5": [
        "Heating: turning a scheduled thermostat up (by voice, in the app or on the thermostat) now lasts its full time — 3 hours by day, 1 at night, as set in Return after a change — even if a period starts or ends meanwhile. Then it goes to whatever the schedule wants by then, rather than back to the temperature from before.",
        "\"Shall I close it?\" after mentioning a window is taken out of the reply too, when nothing in the house can close one.",
        "The nightly self-test no longer counts towards the repeated-replies and speed figures: it asks the same questions every night.",
        "For checking: an admin-only websocket command, sentient/voice/prompt, shows what the voice is given to read for a turn and which parts are biggest.",
    ],
    "1.16.4": [
        "With \"Know what the house can do\" on, an offer to do something no device here can — closing a window, locking a door with no smart lock — is taken out of the reply before it's spoken, the way offers to arm or unlock already are. On 22 Nov it offered to close the bedroom window despite being told it can't.",
        "Asked to lock a door, it now says there's no smart lock it can use, not that the house has no locks.",
    ],
    "1.16.3": [
        "Know what the house can do: it now says there's no smart lock (or motorised blind) it can use, rather than that the house has no locks.",
    ],
    "1.16.2": [
        "The voice is given less to read each turn, so replies can be quicker: the current house context is now short lines rather than a block of data (about a third of the size for a typical turn), and with awareness of the moment on, who got home, the movement and anything unusual are said once rather than twice.",
        "New house scenario checks: every new feature on together, an instant answer remembered in its room, a voice that wasn't recognised never named from a phone nearby, and a size limit on what the voice is given.",
    ],
    "1.16.1": [
        "Instant answers now include the calendar: \"What's on my calendar this week?\", \"Have I got anything on tomorrow?\". Not for guests or in private rooms, where alarms, visitors and the calendar now stay out too.",
        "The Mind page shows how many questions were answered instantly and how long they took against the AI, and how much the voice is given to read each turn, so the new features can be judged on speed.",
        "Three new self-test questions: a room's temperature, two things in one question, and asking it to lock a door when there's no lock it can use.",
    ],
    "1.16.0": [
        "Awareness of the moment (Voice → Conversations): every reply knows who's home and since when, which rooms have movement, what happened in the last 45 minutes and anything unusual for today.",
        "Short-term memory per room: the voice knows what was said, switched and announced in the room it's in over the last hour, and a conversation follows you into the next room.",
        "Know who's probably talking: without voice recognition, a message typed in the app or your phone being the only one in the room tells it who it probably is, and your own things come first. A guess never allows anything.",
        "Instant answers: the time, the weather, whether one device is on or open, a room's temperature, the next alarm, visitors and the lock-up check are answered straight from the house, without waiting for the AI.",
        "Know what the house can do: the voice is told what it can control here, and what the house doesn't have (no locks, no blinds), so it doesn't offer what no device can do.",
        "A memory that keeps itself (Memory & learning): the overnight reflection puts right memories that something said that day shows are out of date, and one guess a day is asked about.",
        "Habits prove themselves (Noticing & learning): a newly learned habit is checked against the days after it was learned and offered only once it has been right on at least 3 of 4.",
        "All of these are off until switched on in Settings.",
    ],
    "1.15.9": [
        "With \"Unified household evidence lookup\" on, the voice is now told to use it first for questions about what happened earlier. It had been switched on since 19 Nov and never used.",
        "A device you've cleared from House health is still offline as far as the voice knows, so it won't say a command to it worked.",
    ],
    "1.15.8": [
        "House health: each problem has a ✕ to clear it. It stays cleared while it carries on, and shows again only if it goes away and comes back. The bell (\"Stop flagging\") is still there to ignore a device for good.",
        "A battery running down faster than it used to is no longer a House health problem. The battery forecast already says when each one will need changing.",
    ],
    "1.15.7": [
        "Watches: \"watch for cars by the gate\" works first time. Asking to look for something picks the camera rather than a door or gate sensor with the same name, the question is worked out from what to look for, and the voice can name the camera as \"camera\".",
        "The Mind sensor is quicker: the camera, incident and learning summaries are worked out once a minute rather than on every voice turn.",
    ],
    "1.15.6": [
        "Where the voice went wrong: questions about the conversation itself (\"What did you say?\", \"Were you watching the gate?\") are no longer logged as answering about the past without looking, and looking at a saved camera picture now counts as looking. Notes already kept are checked again with the new rules when Sentient starts.",
    ],
    "1.15.5": [
        "Room temperatures on the House page now come from real room sensors. A voice speaker's, camera's or tablet's own temperature (for example a Satellite1's chip) is no longer shown as the room's, and nor is any sensor named after a CPU, chip, board or the device itself.",
        "You can choose exactly which sensor gives each room its temperature and humidity in Home Assistant: Settings > Areas, labels & zones > the area > Temperature sensor / Humidity sensor. When one is chosen, Sentient uses only that sensor. Hover over a room's temperature to see where it comes from.",
    ],
    "1.15.4": [
        "Someone stepping out of the front door is no longer logged as a visitor. On 20 Nov at 07:53 the camera saw them a fraction of a second before the door opened, so the briefing said someone had been \"in the yard\". If the door opens within 15 seconds of the camera seeing someone, and nobody rang, it's noted as someone from the house going out: kept off the briefing, today's visitors and the phone. The doorstep announcement itself still plays straight away.",
        "The morning briefing no longer repeats things: a problem, job or to-do item it told you on an earlier day isn't given to it again while it's unchanged. A new item, or one whose wording changes, is said. What's open right now is always said.",
        "Descriptions of the front door camera now say \"at the front door\" or \"by the front door\", never \"the yard\".",
    ],
    "1.15.3": [
        "The voice can read battery levels: \"how much battery has the back door sensor got?\" or \"any batteries running low?\" now gets the level and how long it should last. Battery sensors don't need exposing to Assist.",
        "The evening look at tomorrow is shown what's already been said today, so a reminder about the bins at 7 isn't followed by the same news at half past.",
    ],
    "1.15.2": [
        "Backup voice: when the backup you've chosen is another AI and it can't answer either (no internet, say), a command like \"turn the light off\" now goes to Home Assistant's own agent before Sentient gives up, so lights and heating still work by voice. Questions don't, because it guesses at them (asked \"what's the news\", it gives the time).",
    ],
    "1.15.1": [
        "Fixed: when a voice conversation had an empty reply left in it, Google refused every later turn (\"content parts are required\") and the backup answered instead. That happened on 19 Nov when \"no thank you\" came after a camera question. The empty reply is now mended and the question asked again, once.",
        "Choosing which of two duplicate memories to keep is no longer read out in the evening as \"I've updated what I know\".",
        "A self-test answer that today's checks pass no longer stays on House health until the next run (e.g. \"I can't check what you ate\" counted as not knowing). Answers that switched something, had a tool fail or offered to arm or unlock still count as wrong.",
    ],
    "1.15.0": [
        "Incident judgement can learn who it was: someone from the house who came home in the five minutes before, left within ten minutes after, or whose face Frigate named, now counts as household evidence for the cognitive core. Until now it agreed on none of the incidents marked \"that was us\", because nothing it saw said who it was, so it could never earn its way out of watching. It still only watches until it's earned it, and it can never hold back an alert or make a concerning incident less serious. It uses Home Assistant people, so add a person (with their phone) for everyone in the house.",
        "Memory looks after itself better: every night the same thing remembered twice becomes one (the pinned or best-known one is kept), with no AI. The overnight reflection no longer files one-off events (\"the car left the drive at 9:34 this morning\") as memories, as those belong on the timeline.",
        "The weekly tidy no longer leaves duplicates it isn't allowed to merge (a pinned memory, or two filed under different kinds). It puts them in Memories → check-up for you to choose which to keep. Each tidy is now noted on the timeline, even when nothing changed.",
    ],
    "1.14.0": [
        "Fewer repeats: an overnight thought or something nobody heard isn't passed on if it's already been said out loud in the last 12 hours, so the bin isn't said by the briefing and then again seconds later. The thoughts the briefing is saying are held back while it speaks. Doorbell, gate and urgent alerts are never affected.",
        "The morning briefing is shown what yesterday's said, and leaves out anything unchanged that isn't due today, so the same problems aren't read out every morning.",
        "Repeated replies are counted: with the voice timings shown, the Mind tab counts replies that start with the same words or repeat a whole sentence from the day before. Only short hashes are kept, never the words.",
        "New, off by default: \"Say things differently each time\" (Voice → Conversations) shows the voice its own recent replies so it doesn't give the same one word for word.",
        "New, off by default: \"Recall by meaning\" (Memory & learning). One small AI call a night gives each memory a few related words, so \"fancy a cuppa?\" finds the kettle memories. No embedding server needed.",
        "With \"Keep each person's memories to them\" on, the memories you told it about yourself are always given to the voice first when it knows your voice. Recall no longer matches short words hidden inside longer ones (\"up\" in \"cuppa\").",
    ],
    "1.13.0": [
        "Settings, tidied: 17 reliability and safety checks are now always on and no longer shown as switches. These are: rechecking and recovering actions, asking \"which one?\" when unsure, explaining why it did something, the live voice check, delivery history and the speech-failed alert, the voice mistakes log, self-check, duplicate, routine and Frigate checks, auto-off holding back after a voice command, the camera pre-filter, hiding camera sub-streams and the battery forecast. Any that were off are turned on.",
        "Settings, folded: each page opens with its main switches. The settings that only matter once a switch is on sit in a closed \"Fine-tune\" section, and rarely changed ones (diagnostics, trials, AI budget, semantic memory, page options) are in a closed \"Advanced\" section. Nothing else has changed, and every setting keeps its value. About 165 switches was too many to go through: a page now opens showing about 60.",
    ],
    "1.12.9": [
        "Vacuum: stopping off its dock (remote control, a pause) no longer counts as stuck or says it's finished each time. A run finishes after 10 minutes stopped, and it only counts as not making it back if it's still off its dock after an hour. Earlier counts, which included remote-control sessions, are cleared.",
        "Voice: \"What did you say?\" now repeats the last thing said out loud in that room.",
        "Voice: asking a camera to look without saying what for (\"Can you see the rabbits?\") now just looks, instead of failing.",
        "Self-test: an honest \"I can't check that\" is marked right for questions it can't know.",
        "The voice mistakes log counts reading the calendar as looking, and a near-miss word in the voice's notes no longer fails it.",
    ],
    "1.12.8": [
        "Overview: \"The day so far\" only counts what was actually said. Messages held back (for quiet hours, say) no longer count as said.",
        "Timeline: the same message held back again for the same reason is one entry that shows how many times and when it was last held, instead of a new line every retry.",
        "Rooms: a speaker's own occupancy sensors (like a Waveshare's \"Is speaking\") no longer count as someone in the room. So a speaker talking doesn't make its room look occupied, or the house look awake at night. A camera's motion sensor on a speaker or tablet still counts.",
    ],
    "1.12.7": [
        "Announcements: when an announcement also listens to doorstep sensors, its 'Don't say it if this opened just before' setting now only skips those sightings. A doorbell press is always said. Set it to the front door and someone going out is no longer announced as \"someone at the front door\", while a visitor ringing still always is.",
    ],
    "1.12.6": [
        "Offers: say yes to \"Shall I put the kettle on?\" and it now says \"Okay, I'll put the kettle on.\", not \"Request sent.\"",
        "Fixed: a phone notification sent to a named phone made the house's change log throw an error, so that change wasn't recorded.",
        "The Mind sensor does less work each time it updates.",
    ],
    "1.12.5": [
        "Voice: calendar questions (\"What's on this week?\") are now answered by Sentient's own calendar reader. It works out each event's day itself (\"Saturday 21 November, all day\") instead of leaving the AI to work it out, which got all-day events a day out. It reads the calendars you've exposed to Assist, and none of it is shared with guests or in a private room.",
    ],
    "1.12.4": [
        "Motion lights: new per-light switch, \"Stay off after it's switched off by hand\". Leave it on for bedrooms, so a light you switch off stays off while you're still moving about. Switch it off for rooms like the kitchen, so walking back in turns the light on again. On by default, as before.",
    ],
    "1.12.3": [
        "Voice: all-day calendar events are said on the right day. Before, an event on Saturday could be read out as \"Friday to Saturday\", because a calendar stores an all-day event as ending the next day.",
    ],
    "1.12.2": [
        "Room trackers: the voice can now answer \"Where's my phone?\" or \"Where's [name]?\" from the room tracker. It says where the phone is (a phone isn't always with its owner), and says it can't tell when the phone isn't seen. Not shared while guests are visiting or in a private room.",
        "Self-test: when a room tracker knows where a phone is, the household questions include \"Which room is it in?\".",
    ],
    "1.12.1": [
        "Room trackers: a room whose speaker is a backup one (only used when the main rooms are empty) now gets the message when your phone is in it. Before, it went to wherever the motion was instead.",
    ],
    "1.12.0": [
        "New, off by default: room trackers. Point Sentient at a Bluetooth room sensor (such as Bermuda's \"Area\" sensor for your phone) and things meant for wherever people are get said in the room you're in. When it can't tell — phone not seen, you're out, or that room has no speaker — it uses motion as before. It's only used to choose where to speak, never to decide whether anyone's up. Settings → Voice → Speakers & volume.",
    ],
    "1.11.17": [
        "Voice: when you tell it something happened (\"it was packed up just after one\"), it checks before agreeing, and if you question an answer it looks again rather than just changing its answer to match yours.",
        "Voice: a conversation ends on the answer — no more \"Is there anything else you'd like me to check?\" after each reply. It still asks a real follow-up when there is one.",
        "Voice: when it saves a memory it fixes obvious mishearings, uses the names it already knows, asks if it couldn't make out what you said, and writes a date rather than \"today\".",
        "Memory: pinning a memory that says \"today\" writes the day in, as pinned memories never expire (memories already pinned are put right too). Overnight guesses filed as corrections are now marked as guesses for the voice.",
        "Mind page: how conversations go (follow-ups, corrections, timings — never the words) is now kept over a restart, so it builds up. Switching off \"Show voice timings\" clears it.",
    ],
    "1.11.16": [
        "Motion lights: a light someone switches off by hand now stays off while the room's in use, and motion turns it on again once there's been no movement for ten minutes. It used to come straight back on with the next movement (live: a bedroom light switched off came back on five seconds later).",
    ],
    "1.11.15": [
        "Voice: asked \"Is it on?\" with nothing said before, it now asks which one you mean instead of guessing a device (it answered about the coffee machine). \"Is it raining?\" and \"What time is it?\" are answered as before.",
        "Voice: a time like 08:34 is no longer rounded to \"half past eight\".",
    ],
    "1.11.14": [
        "Self-test: \"When was the front door last closed?\" now checks against the last time the door was really opened and shut. It used the sensor's last report, so a sensor that said \"closed\" again after a reload moved the expected time and a right answer was marked wrong.",
    ],
    "1.11.13": [
        "Where the voice went wrong: a reminder, watch or announcement that worked is no longer logged as a voice problem (\"reported completion without a recognised success result\"), which 1.11.11 started doing. One that says it isn't finished yet is still noted, and \"done\" after a tool failed is still a false claim.",
        "The \"Unified household evidence lookup\" setting now has a description, and French text.",
    ],
    "1.11.12": [
        "Self-test: a plain \"Yes, it is.\" or \"No, it isn't.\" now counts as a right answer to \"Is the light on?\". It was marked wrong even when it was right, which put a false self-test failure in house health.",
        "Self-test: it now sees which tools the voice used for each answer. Before, every answer showed none, so a failed look-up (like a camera check) could still be marked right.",
        "Self-test: the most-asked question is no longer asked a second time when it's already one of the checks (\"Is the alarm on?\" came up twice).",
    ],
    "1.11.11": [
        "Guarded voice agents can now use the read-only look-ups too (history, why something happened, camera activity), with the same privacy checks; switching things is still guarded.",
        "Where the voice went wrong: answering about the past now needs a look-up that actually covers the past, not just any tool, and two people asking the same thing in different rooms are no longer merged into one.",
        "New optional setting, off by default: one look-up that checks the house record, timeline and cameras together.",
    ],
    "1.11.10": [
        "Where the voice went wrong: a script that reports it worked (like showing a camera on the Echo Show) now counts as having done something, instead of \"said it was done, but nothing was done\".",
        "Voice requests through Sentient Voice Recognition were counted twice (the request, then the relayed answer with no tools). The same question and answer within seconds now count once, judged by the copy that shows the tools used.",
        "Questions about what was seen or happened (\"when did you last see the rabbits?\") are looked up in the camera record every time, not answered from an earlier reply.",
    ],
    "1.11.9": [
        "Weather warnings: when the warnings feed can't be reached, the voice is told so in plain words (\"Couldn't read the warnings just now\") instead of being handed a raw technical error it could read out.",
        "Replay benchmark (for testing changes against recorded days): a day with a restart in it no longer starts devices as \"unavailable\", and a replay no longer hangs on a real-time wait inside Sentient.",
    ],
    "1.11.8": [
        "Never offer to arm, disarm or unlock: when the offer was the reply's question, the speaker no longer keeps listening for an answer to it, and the conversation no longer holds it as an unanswered question.",
    ],
    "1.11.7": [
        "New: Never offer to arm, disarm or unlock (Settings → Voice → Personality & house rules; off by default). With it on, a sentence offering to arm or disarm the alarm or unlock a door (\"Shall I arm it in away mode?\") is taken out of the voice's reply before it's said — including a reply spoken as it's written, which is held back a sentence at a time — and out of what the conversation remembers saying. Someone who asked to arm, disarm, lock, unlock or open still gets their confirmation question, and a reply that's nothing but the offer is left alone rather than going silent.",
        "The overnight self-test uses the same check, so it marks exactly the replies this would have changed.",
    ],
    "1.11.6": [
        "Asked the same thing again, the voice looks the answer up afresh instead of repeating what it said earlier — so an answer that was vague or wrong the first time isn't copied.",
    ],
    "1.11.5": [
        "Times are said to the minute: when the house knows when something happened (a car arriving at 3:12pm), the voice says \"twelve minutes past three\", not \"just after three\". Things from yesterday are called yesterday rather than by the date.",
    ],
    "1.11.4": [
        "The drive in voice answers: \"what time did she get back?\" now gets the time her car arrived. The voice is told the drive's last change (whose car came or went, and when), the timeline it can look things up in now includes cars arriving and leaving, and questions about getting back, getting in, arriving or going out bring that timeline along. Before, it only knew what was parked right now.",
    ],
    "1.11.3": [
        "Where people are: movement in an outdoor area (a doorbell camera seeing people walk past, the garden) no longer counts as someone being in the house. Before, the front door camera could be the \"likeliest room\", so Sentient thought someone was up and about when they weren't and the morning briefing could wait for a room nobody was in. Uses the Outdoor areas setting.",
    ],
    "1.11.2": [
        "Camera questions: when a camera isn't exposed to Assist, the voice is now told why it couldn't look and not to answer \"nobody\" anyway — before, every camera question failed this way and still got a confident \"no one was seen\". House health names any camera Sentient keeps a record of that Assist can't see.",
        "Self-test: an answer whose tools failed (and weren't put right on a retry) is now a fail, as is offering to arm, disarm or unlock when only asked. The self-test's own questions no longer count in \"Where the voice went wrong\", and the tools it used are shown again.",
    ],
    "1.11.1": [
        "Learned bedtimes can be forgotten (admin command sentient/sleep/forget), for when nights were recorded wrongly — say by a motion sensor that flickered all night — and the usual bedtime came out at 05:25. It starts learning again that night; which sensors are restless at night is kept.",
    ],
    "1.11.0": [
        "New on the Mind page: \"Where the voice went wrong\". Every voice turn is checked with plain rules, no AI: a tool that failed (and whether a retry fixed it), a device that didn't do it, saying something was done when nothing was, offering something no device can do, not being able to answer, or answering about the past without looking anything up. It shows how many turns this week went without a problem, so each update can be judged by whether that number goes up. Settings → Voice → Checks & diagnostics; on by default; switching it off clears what was kept.",
        "The overnight self-test now asks household questions too, built from your own devices: when a door or window last closed, whether a light is on, whether a camera saw anyone, whether anyone came to the door today, something it can't know (an invented answer fails), something no device can do, and a question too vague to answer without asking which. Switching anything while only being asked is a fail. The score and recent runs are on the Mind page (\"Household questions\"). Settings → Mind → Self-checks & privacy; on whenever the self-test is.",
    ],
    "1.10.5": [
        "Heating: a thermostat that drops back to its own app schedule by itself is put back in its hold after ten minutes, not the hour or three a change by hand gets, so its own programme can't undo the morning warm-up.",
        "Visitor log: the door camera's picture is taken the moment someone reaches the doorstep or rings, not a few seconds later, so quick callers aren't logged as \"nobody at the door\".",
    ],
    "1.10.4": [
        "Announcements: one that's skipped when a door opened just before (the gate after the back door) is now also skipped while that door is still standing open, since someone's in and out of the garden. It's on for existing announcements; switch it off per announcement with \"…or while it's still open\".",
        "Motion lights: if movement turns a light back on within five minutes of it going off, someone was sitting still, so it waits twice as long next time, then four times, until it's been off for half an hour. On for every light; switch it off per light with \"Wait longer if it comes straight back on\".",
        "Announcements can have other triggers that mean the same thing: \"…or when any of these turn on\". Add the doorstep camera zone to the Doorbell announcement and whichever comes first, the bell or someone reaching the doorstep, is announced; the other within \"Ignore repeats for\" is the same visit and stays quiet.",
        "A device used by hand all day (the kettle) is noted on the timeline as an unexplained change once a day, not every half hour, and the count says what it means: \"the third day this week\".",
    ],
    "1.10.3": [
        "Fixed: a motion sensor that's fine by day but never rests at night (a cat asleep in its view) kept the house \"up\" until it went quiet near dawn, so the usual bedtime was learned as 05:25. It's now set aside at night while its room is dark and quiet; with a light on or something playing in there, it still counts as someone up. What it learns about the night is kept across restarts.",
    ],
    "1.10.2": [
        "Security page: each incident card shows its camera picture, the camera, what was seen and the zones, says in plain words what its stage means (\"Observed · one report so far\"), and asks \"What was it?\" — one of us, a visitor, nothing or an animal, worrying, or not sure. A camera visit that's part of an incident is shown on that card, not twice.",
        "Marking an incident \"nothing / an animal\" also marks its camera events \"not a person\", so the HIDE THIS ZONE suggestion learns from the incident cards too.",
        "Fixed: house changes waiting to be saved could be lost when Home Assistant restarted; they're now saved while it can still write.",
        "Fixed: tapping the heating up several times in a row is one adjustment with one time limit, not one per tap, and it goes back to where it was before the first tap.",
        "Fixed: going back to bed soon after getting up (within three hours) is the same night, not a new bedtime. Up at 5:30 and back to bed was being learned as a 5am bedtime, so the usual bedtime read \"06:15\". Nights already saved are joined up the same way when it starts.",
        "Ask \"has anyone been to the rabbits today?\" or \"when was someone last in the garden?\" and the voice now checks the camera record (every visit, with times and zones) instead of taking a picture now and saying nobody's there. Named spots like the rabbit pen work too.",
        "New setting, Settings → Safety & security → Front of the house: cameras and sensors listed there are never grouped into one security incident with anything at the back, so the doorbell and the back gate at the same moment are two incidents, and Linked events no longer joins them.",
        "The voice no longer offers to do physical things it can't (\"Shall I pop out and feed them now?\"); it offers what it can do instead, like a reminder or a look on the camera.",
        "Camera records now keep up to 3,000 detections (about a week) instead of 200 (two or three days in a busy house), so questions about earlier in the week can be answered.",
        "Well before the usual getting-up time, the house is only marked up when movement crosses three rooms, or two with a light switched on by hand. Pets crossing the hall and kitchen at 5am no longer switch the Echos back on.",
        "A security alert on your phone is updated in place once the camera picture has been checked: \"Checked the picture: no one visible, so possibly a false alarm\" or who was there. The alert itself still goes first and is never withdrawn.",
        "Good news (\"battery fine again\", \"is back to normal\") stays on the timeline and no longer goes to phones as House health.",
        "The overnight learning note no longer says \"tested beliefs: 0 held, 0 missed\" on nights there was nothing to test.",
        "Fixed: a watch that ends with nothing seen says \"Nothing came of watching the rabbit pen for an hour\", not \"watching for Watch the rabbit pen…\".",
        "Fixed: the Mind sensor's camera lists no longer go into Home Assistant's history (it was over the 16 KB limit and logged a warning every minute).",
    ],
    "1.10.1": [
        "Heating schedule: a thermostat running its own built-in programme (a Smart Life one can't have it switched off in the app) is now put into its manual hold by sending the mode on its own, which it accepts, rather than with the temperature, which it ignored. If it still won't hold, the timeline says so.",
        "Settings: Never touch is under System, and Safety & security has its own Safety section with Night watch, instead of both sitting under Cameras.",
    ],
    "1.10.0": [
        "Security page: camera events now follow your Frigate zones. On a camera with zones, only something that entered one is listed or starts a security incident; walking past outside every zone no longer does. Settings → Camera security → Which camera events are listed.",
        "Events on one camera within a minute of each other are one visit: one card with the time, what was seen, the zones it went through and a thumbnail.",
        "Three buttons per visit: GOOD CATCH, NOT A PERSON and IGNORE. A reviewed visit folds away into REVIEWED, and so does a reviewed incident. NOT A PERSON also counts as a reviewed false detection for an incident made only of that visit; a review you gave yourself is never replaced.",
        "After three NOT A PERSON in one zone, the Security page offers HIDE THIS ZONE: movement only in that zone stops being listed. Frigate isn't changed, and the zone can be listed again in Settings.",
        "The setup doctor is tidier: a one-line verdict at the top, anything to fix first with what to do, then each area as its own section of small tiles, with a detail they all share said once.",
        "A heater or kettle changed at the device while someone is up and moving about is noted on the timeline, not said or sent to phones as house health. Overnight, or with nobody moving, it's said as before.",
        "A self-test that gets every answer right is noted on the Mind tab instead of being sent to phones; a wrong answer still is.",
        "Heating schedule: a thermostat found away from the schedule (for example running its own built-in programme) is no longer put back to the night temperature when a period starts within half an hour, and the timeline names today's programme rather than the first one saved.",
    ],
    "1.9.0": [
        "Voice routines now check requested final device states and report failures. Already-satisfied states are distinguished from changes; unsupported or conditional steps cannot be proven executed from a matching state alone.",
        "Only successfully changed lights and switches in explicitly voice-requested routines get temporary person-request priority. Automated routines and unchanged devices do not.",
        "Optional text-only AI routing reports service reliability per job kind and demotes weak alternatives. An administrator can explicitly reset job penalties from calibration; no background paid probes or claim of answer-quality validation.",
    ],
    "1.8.0": [
        "The cognitive core now correlates an observed entry opening with a later observed person detection, but does not treat that timing as proof of household identity. A doorbell or doorstep visit remains a caller.",
        "The Mind tab scores the cognitive core and the usual rules side by side on your reviews: household incidents got right, others wrongly called household, and threats called less serious.",
        "The experimental night heads-up control remains inactive even if selected: a gate opening and camera sighting cannot verify a household member. Spoken and phone security alerts stay unchanged until independent identity evidence is safely supported.",
    ],
    "1.7.0": [
        "The Mind tab now shows how conversations go: how many follow-ups carried on from the last thing you asked, how often it had to ask which thing you meant, how often an answer was corrected (\"no, I meant…\"), and how quickly it answers. With \"Show voice timings\" on; no words are kept.",
        "Memory indicators now show approximate word overlap and search/correction counts; this is not verified answer accuracy.",
        "Every memory now says how it's known — confirmed, told, said, or a guess worked out overnight. Guesses are marked on the Memories page, and the voice is told to check before relying on them.",
        "HOW I'M LEARNING shows whether a corrected topic was heard again during a fortnight; no repeats does not prove the situation arose or that learning was correct.",
    ],
    "1.6.0": [
        "The cognitive core can now decide what stage a security incident is at — what the Mind tab and the voice say it is — once your reviews show it gets each kind of incident right (Settings → Safety & security, off by default). It never changes or holds back an alert, never calls an incident less serious than the usual rules' \"concerning\", and stops by itself if later reviews fall short. Review incidents from the Mind tab: REVIEW THIS INCIDENT.",
        "Settings → Setup doctor → DOWNLOAD THE LAST DAY saves what happened in the house over the last 24 hours and what Sentient decided, so a new version can be checked against your own days before you install it. Nothing is sent anywhere.",
        "Fixed: the Mind tab's \"How I'm learning\" card, which hadn't drawn since 1.5.0, and a link from another tab to a Settings page (such as \"turn it on in Settings\" on the House tab) opening the Settings front page instead.",
    ],
    "1.5.14": [
        "Settings: Doorbell & AI looks and This page open again for administrators (1.5.12 and 1.5.13 broke them).",
    ],
    "1.5.13": [
        "No edit button on the tabs any more: with \"Rearrange the Sentient page\" on, your layout is always used, and you change it from Settings → This page → EDIT PAGE LAYOUT. The edit toolbar shows only while you're editing, until DONE EDITING.",
    ],
    "1.5.12": [
        "Rearrange the Sentient page: switch it on in Settings and an EDIT LAYOUT button lets administrators move, hide, show again and reset cards on the Overview, House, Mind and Security tabs. Saved in Sentient, so every screen shows the same. Off by default.",
        "Motion lights: a rule can be set not to switch a light back on for two minutes after you turned it off by voice. Auto-off can likewise wait after a voice \"on\". Both off by default.",
        "Security incidents on the Mind page can be marked household, visitor, false detection and so on. This only records your review; no alert settings change.",
        "Optional, all off by default: local text reading (OCR) for scanned PDF manuals if Tesseract is installed on the Home Assistant host; better memory search through your own local Ollama embedding server; a cheaper second AI for background text jobs once both have a measured track record; and a before/after comparison of a heating kWh meter.",
    ],
    "1.5.11": [
        "Something switching itself at its usual time (the kettle turning off after it boils) is noted on the timeline, not sent to phones as house health. Changes that are unusual for the time are still said and sent.",
        "The evening \"something I've learned\" no longer reads out notes about how Sentient should talk, and doesn't say \"Something I've learned: I've…\" twice over.",
        "The doctor no longer warns about the person sensors of cameras behind the fence (the ones under \"Inside the boundary\"): they only see your own property, so the whole camera is right for them.",
    ],
    "1.5.10": [
        "Forgetting something now removes it from Sentient's searchable copy straight away, not at the next sync ten minutes later.",
        "The same announcement from two parts of Sentient, worded the same apart from punctuation, is said once. Critical alerts are never held back.",
        "Asking about something by another name (\"boiler\" when the memory says \"radiator\") now finds it, using your own word list. No AI call.",
        "Diagnostics now show which parts of Sentient are working, degraded or not yet checked (voice, cameras, heating), what Sentient's own backups contain, and when two parts of Sentient asked for opposite things at once (for example voice on, then motion lights off). Watching only: nothing is blocked or changed.",
        "Heating: the learned warm-up speed is checked against later warm-ups, and the error is shown only once there's enough history. No saving is claimed and the heating isn't changed.",
    ],
    "1.5.9": [
        "The morning briefing is given once a day even if Home Assistant or Sentient restarts in the morning. Before, the day it was given was forgotten on a restart, and a motion sensor coming back after the restart counted as someone walking into the room, so the briefing came again.",
    ],
    "1.5.8": [
        "Music through Alexa: without Music Assistant, \"play something calm in the kitchen\" can ask that room's Echo to play it from Spotify (or Amazon Music, Apple Music, Deezer, TuneIn, iHeartRadio) — by name, a playlist, artist, song or mood. At the night volume in quiet hours; \"stop the music\" pauses it. Off until switched on: Settings → Voice → Music through Alexa. When it's on it's used even with Music Assistant installed, which can't play Spotify without Premium.",
    ],
    "1.5.7": [
        "Someone up early no longer gets the morning briefing during quiet hours: it waits until they end, then comes the first time they're seen moving (in the morning briefing rooms, if chosen). The old way is a switch: Settings → Briefings → Morning briefing in quiet hours.",
        "Things held back for quiet hours are passed on as \"Overnight: …\", never \"While you were away\" — nobody had gone out. Something nobody heard is \"Earlier: …\".",
        "A newer reading of the same thing (the hutch at 0°, then -1°) replaces the older one instead of both being said, and in the half hour after quiet hours the morning briefing goes first, so what it covers isn't said again.",
        "A remark that talks about a different room from the one where something changed (the bathroom window opening, then \"the air quality in the living room is poor\") is held back instead of said.",
        "A remark never claims the house did something itself (\"so I have switched on the air purifier\" when it had come on by itself): a remark can only speak, so one that says otherwise is held back.",
        "When the doorbell camera looks and sees nobody (\"empty street view\"), no \"At the door\" notification is sent to phones.",
        "The Setup doctor warns when camera security uses a sensor that covers a whole Frigate camera while that camera has zones: people walking past in the street set it off. It names the zone sensor to use instead.",
    ],
    "1.5.6": [
        "Fixed: saving one setting through Sentient's settings commands no longer resets the other settings on that page to their defaults (the Settings page itself was never affected).",
        "House health's check for devices listed twice reads Home Assistant's device list the current way (the old way logged a warning and would stop working in 2027.9).",
    ],
    "1.5.5": [
        "Live camera: Home Assistant's own player that loads but never shows a picture now falls back to the still picture after 12 seconds, instead of staying black.",
        "Security incidents: a door and a camera only count as backing each other up when they're different devices, and evidence with no clear status is treated as unknown rather than settled.",
        "Long paragraphs in text manuals are split into sections instead of losing their end; voice recognition no longer suggests a match level from broken or ambiguous scores.",
        "For testing, off by default: \"Compare security incidents\" (Settings → Safety & security) judges each incident a second, stricter way alongside the usual stages and counts how often they agree, as totals on the Mind sensor. It never changes an alert, a stage or what is said.",
    ],
    "1.5.4": [
        "Manuals say which page: each PDF is read page by page once, on this machine, and a question sends the AI only the few pages that match instead of the whole document — far cheaper — so the answer can say \"it's on page 14\". A manual is also found by what it says when its name doesn't match, and with the AI down the closest passage is read out with its page. Scanned PDFs are still read whole (the Setup doctor lists them). Switch: Settings → Manuals & documents → Read PDFs page by page.",
    ],
    "1.5.3": [
        "A device flickering between states (an Echo going playing, idle, paused in a second) counts as one unusual event, not three, on the Mind tab and in what the voice is told.",
        "Fixed: \"Stop flagging\" on a device that shares its name with an empty copy (a plug in Tuya Local and again from Tuya's cloud) said there was nothing to ignore; it now picks the real one.",
        "House health spots a device listed twice under one name where one copy is empty, with a button to disable the empty copy so it stays out of the way (Settings → House health → Spot devices listed twice).",
        "The Mind tab's learning card lists the devices that switched by themselves most this week.",
        "After a week of watch mode, if skipping remarks about what's usual would have saved 20 or more AI calls, Sentient suggests switching it on (once, in the timeline).",
        "The Mind sensor no longer works out the self-check during its update; it shows the last one and refreshes straight after, so start-up is quicker.",
    ],
    "1.5.2": [
        "Restarts no longer count as devices switching by themselves: everything coming back from unavailable is left out of that count and of the patterns Sentient forms overnight, so a restart at the same time on a few days can't become a belief.",
    ],
    "1.5.1": [
        "The Mind tab's learning card counts properly: a script finishing is no longer taken as someone putting Sentient's change straight back, \"switched by themselves\" counts controlled devices only (not every sensor reading), answers straight from the house count as done without the AI, and watch mode shows how many AI calls skipping the usual would save.",
        "Fixed: when the AI is down, \"when's my next alarm?\" and \"any reminders?\" are answered from the house again (the reminders list was read the wrong way, so no answer came).",
    ],
    "1.5.0": [
        "The house ledger: every change to every device is now recorded with what caused it — a person, a voice request, an automation, Sentient, or nothing in Home Assistant (the device itself or its app). Ask \"when did the heating go off, and why?\".",
        "Sentient now learns what's usual at each hour and notices what isn't. The Mind tab's HOW I'M LEARNING card shows how much of each day it expected, and that number should rise week on week.",
        "An important device that changes by itself is mentioned once, and repeats are linked: \"the third day this week, always around 11am\".",
        "Memories, conversations and the timeline are kept for a year in Sentient's own database, and recall searches all of it.",
        "Names are said the way they're pronounced: \"Siobhan is pronounced Shivawn\" (a memory, or Settings → Voice → Pronunciations) changes what the speakers say, while the chat and timeline keep the real spelling.",
        "New on Settings: Memory & learning, Weather warnings (your own country's service) and Extra skills (read-only email, calendar clashes, a security check and automations from plain English).",
    ],
    "0.88.2": [
        "Sentient is in the Home Assistant Marketplace (Settings → Marketplace, formerly HACS); updates show under Settings → Updates.",
    ],
    "0.88.1": [
        "Alarms and automated routines from before 0.88 are checked when they go off, like ones made by voice. If one of yours made on the Alarms page is skipped, open it there and save it once.",
    ],
    "0.88.0": [
        "Stories read with a reading voice no longer pause between parts — the next part is made while this one is read.",
        "Phone notifications now show what they're about, and tapping one opens that moment on the timeline. The timeline has a SENT TO PHONES filter to look back over them.",
        "Alarms and automated routines made by voice that switch things are read back before they're saved, can only use what voice assistants are allowed to control, and are checked again when they go off.",
        "This card: what's new after each update.",
        "The set-up wizard asks about people and phones, how much the house does by itself, private rooms and the world outside — run it again from Settings to see.",
        "Settings → Brain & system → How it's used: what you use, what you never have, and a simpler page for new houses.",
        "A readable copy of everything it knows and does (Backup → Readable copy), as well as the restore file.",
        "Start-up is lighter: optional parts that are switched off don't start, and diagnostics show how long start-up took.",
    ],
    "0.87.0": [
        "Automations can pass things through Sentient with sentient.notice — same quiet hours, same judgement.",
        "Outside information (energy price, weather warnings, parcels) in the briefing; a parcel out for delivery means the door is watched all day.",
        "Maintenance page: filters and descaling due by how much things have actually run.",
        "With a time-of-use tariff, a nudge to use the dishwasher's delay start when electricity's cheapest.",
        "With Music Assistant: \"play something calm in the kitchen\".",
        "Questions can be answered from your phone when nobody's near a speaker (phone buttons → Questions).",
    ],
    "0.86.0": [
        "Text it reads from the web, documents or cameras can't make it switch anything.",
        "Brain & system → What leaves the house, with \"forget the last 48 hours\".",
        "Holiday mode: quiet, away lights, heating down, back to normal when you're home.",
        "Choose whether the house just does things, says so first or asks — per kind of device.",
        "Rooms page: private rooms, a gentler voice, own volume and quiet time.",
    ],
    "0.85.0": [
        "Pin memories; the rest are chosen by what you asked.",
        "Ask \"why did you say that?\" — it gives the reason it noted at the time.",
        "\"No, the other one\" puts it right in one go.",
        "Habits that follow sunset or getting up; habits done together offered as one.",
        "The Mind page can suggest a small change to its settings — only changed if you say yes.",
    ],
    "0.84.0": [
        "Setup doctor (Brain & system) checks the AI, speakers, voices and cameras and says what to fix.",
        "Plain yes, no and \"it already does that\" answers to its own questions, without the AI.",
        "It notices when parts of itself keep failing.",
    ],
}


def _key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in version.split(".")[:3])
    except ValueError:
        return (0,)


class WhatsNew:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.whats_new")
        self.seen: str | None = None
        self.version = "0"

    async def async_start(self) -> None:
        self.version = str((await async_get_integration(self.hass, DOMAIN)).version or "0")
        data = await self._store.async_load()
        if data is None:  # a new install: nothing to catch up on
            self.seen = self.version
            self._save()
        else:
            self.seen = data.get("seen")

    def async_stop(self) -> None:
        return

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"seen": self.seen}, 1)

    def listing(self) -> dict[str, Any]:
        if not self.options.get(CONF_WHATS_NEW, True):
            return {"version": self.version, "releases": []}
        since = _key(self.seen or "0")
        releases = [{"version": v, "items": items} for v, items in sorted(NOTES.items(), key=lambda kv: _key(kv[0]), reverse=True)
                    if since < _key(v) <= _key(self.version)]
        return {"version": self.version, "releases": releases[:4]}

    def mark_seen(self) -> None:
        self.seen = self.version
        self._save()
