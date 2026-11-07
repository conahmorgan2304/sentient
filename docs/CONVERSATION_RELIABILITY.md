# Conversation reliability — 0.92.0

This update reduces repeat translation work and makes real voice problems easier to investigate. Existing conversation policy is unchanged: completed single requests close; a question can keep listening; an active discussion stays open until an ending or timeout. New optional controls are in **Settings → Voice** and default to off.

## Remember common language phrases

Enable **Remember common language phrases** alongside both speech-language options. The generic phrase pack prepared by your existing AI is saved in Sentient’s Home Assistant storage and reused after a restart. It contains only common acknowledgements, follow-ups, exact negative/ending phrases and safety labels. It never saves the arbitrary sentence translation cache, names appended to alerts, personal announcements or conversations.

Unrelated Settings changes preserve this pack. Disabling either language feature or the remember switch, changing the selected language or AI Task entity, or using **Forget recent data** clears it. Saved data is validated again when loaded; unsupported versions and mismatched language/provider settings are discarded. Normal settings/storage writes are delayed briefly; integration unload explicitly flushes the store. Clearing invalidates in-flight preparation, so an old result cannot recreate deleted phrases.

Ordinary sentence translations remain memory-only. Simultaneous identical sentences share one translation attempt. A failed or malformed translation uses the original immediately on repeats for 60 seconds, then may retry on the next request. This is demand-driven, not a polling timer. Changing Settings or forgetting clears this cooldown too. Budget checks and the 300-sentence bound remain in force. Only generic preparation is persisted, not this personal-sentence cache or failure history.

Urgent announcements never wait for AI. A restored generic pack can supply translated safety labels immediately after restart. Unsupported or unavailable translations still fall back to the original text.

## Voice response timing diagnostics

Enable **Voice response timing diagnostics** and use the Sentient with backup agent. The **Mind** page displays recent timings for the final agent response:

- Total time inside Sentient’s conversation agent, and whether it finished listening or stopped.
- Main and backup agent calls, when used.
- Sentient tools and guarded action execution, when used.
- Language preparation and translation calls, when used.
- Announcement delivery performed inside the request, when used.

Timings are **inclusive and can overlap**. Main-agent time includes tools; a tool can include translation and announcement delivery. Do not add the stage values together. Missing stages mean they were not observed for that turn; native provider internals are not separately measured. Total time can also include waiting and other work outside those stages.

These measurements do **not** establish microphone/STT duration, time to first audible word, final Assist pipeline TTS time, actual speaker playback or whether somebody heard it. Existing delivery receipts and manual playback feedback remain separate.

Only the last 20 timing records are kept in memory. They contain stage names, durations, outcome, timestamp and listening state—no request/reply text, device IDs or user IDs. They are excluded from recorder history. Guest mode hides them and prevents new recording; Settings changes, forgetting and unload clear them. A turn finishing after a clear cannot repopulate the list. Disabling the switch hides the timing display and stops collecting it.

## Live voice checks

The existing **Live voice check** switch controls the expanded checks. From **Mind**, choose:

- **Discussion:** ask about system health, then end the conversation.
- **Single reply:** ask it to say “ready”; it should finish without opening another question.
- **Question:** ask it to ask you a question, then end the exchange.
- **Cancel:** say the cancellation phrase and check that it stops listening. This is a simple cancellation-ending check, not proof of interrupting a busy microphone or stopping an action already dispatched.

End any earlier discussion before starting a new check. Follow the displayed phrases; speech-language support translates the fixed test prompts through the normal budget/cache path. First use may involve translation calls. If translation is unavailable, the displayed original phrase is the one to use. Matching accepts normal punctuation differences; this is not unrestricted translation evaluation.

Checks observe the **final response**, including local endings, early exits and translated replies. They require subsequent replies from the same device/user scope. One-turn and two-turn checks both let you explicitly confirm whether you heard the reply. An agent passing the text/listening check does not prove microphone or playback success.

## Validation limits

Automated regressions use controlled Home Assistant/provider doubles and real integration storage/reload paths. Live microphone capture, TTS pronunciation, network latency and physical playback still require tests in the house. No routine or device actions are replayed just to gather timings.
