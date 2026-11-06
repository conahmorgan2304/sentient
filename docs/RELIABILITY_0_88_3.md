# Reliability fixes in 0.88.3

Sentient now waits for a new user turn before its acting tools can run after reading outside text. Words such as “turn” in “How do I turn off this appliance?” do not grant permission. This protection applies to Sentient tools with an active Home Assistant chat log; it does not control another integration's native tools.

Alarms with device actions or routines require a matching pending schedule and a clear yes in the immediately following user turn, within two minutes, from the same conversation, device and user context. The approval is consumed once. Changed schedules, expired replies, topic changes, model self-confirmation and contexts without a verifiable chat log cannot approve actions. Plain spoken alarms retain their existing behaviour. Existing exposure and action restrictions still apply when creating and running alarms.

Phone questions are invalidated on Settings changes and unload, and check current recipients and enablement when answered. Speaker questions are cancelled when the reply changes topic or corrects the request. “No thankyou” dismisses a question; “yes, but tomorrow” needs clarification. Delayed trust actions recheck the device, never-touch restrictions and settings before running. Listening preferences and the existing Settings controls are unchanged.

Forgetting recent data also clears speech delivery records, session traces and topics, live voice-check replies, overlapping security incidents and follow-up questions. Older follow-up questions without creation dates are cleared conservatively. Pending approvals are cancelled. Forgotten open conversations and speech already in flight cannot recreate their cleared records when they finish. Pinned memories remain; Home Assistant history, external providers and existing backups are outside this operation.

Disabling active holiday mode restores heating. Original targets are saved before frost changes and retained if restoration fails or a thermostat is unavailable. Pending restoration retries every five minutes, including after restart, and is reported on the timeline and in holiday status. A new holiday cannot overwrite a pending recovery. Changed temperature/mode settings supersede the holiday target. Thermostats already off are left alone.

These are reliability fixes, with no new optional feature to configure. Existing optional features remain controlled by Settings.

Validation includes normal conversation endings, same-turn and replayed confirmations, cross-conversation/user/device replies, disabling features with outstanding questions, forgetting while speech is in flight, failed/offline heating restoration, restart recovery and manual temperature changes. Hardware audibility and physical thermostat behaviour still require checking on the installation.
