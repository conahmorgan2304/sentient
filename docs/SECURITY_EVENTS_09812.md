# Security event continuity — 0.98.12

Four optional Settings controls connect existing camera/security capabilities. All default to off; existing AI review switches still control whether cameras/clips are analysed.

| Setting | Location | Result |
| --- | --- | --- |
| Link security alerts to Frigate events | Camera security | Retain event IDs and metadata, link a unique camera/time candidate to an alert, use that event's snapshot where available, and review that exact event after the alert. |
| Keep the security event in the conversation | Camera security | A selected event can be reused in follow-up questions within the same conversation, device/satellite and Home Assistant user context, for up to five minutes. |
| Record feedback on camera events | Camera security | Admins can record useful, not useful, false positive or cat feedback, and clear it. Repeated reviewed person-detection corrections produce a camera-specific review suggestion. |
| Reuse matching clip analyses | Event clips | The automatic review and event-clip services share compatible generic analyses of the same finished event. |

## Event evidence

The new local record listens to Frigate's MQTT event feed when MQTT is configured. Only cameras selected for clip watching, the configured door camera and unambiguously mapped camera-security sensors are retained. If a security alert has no known candidate, a bounded local REST request looks for overlapping person events on that camera. Multiple candidates leave the alert unbound; the original sensor alert is preserved and no unrelated latest clip is substituted.

A person-occupancy sensor does not supply an event ID. A unique match by camera and time is therefore explicitly labelled a **candidate**, not proof that the sensor identified that particular event. Cross-camera events still do not establish identity or a confirmed route.

An event snapshot is fetched by its exact ID, has a bounded download/time limit and is checked for a JPEG header. It is temporary and removed after analysis. If unavailable, the existing current-snapshot path remains an enrichment fallback, with its uncertainty wording. An empty event/current picture cannot erase the sensor detection.

With event evidence enabled, a bound post-alert review requests the exact event ID after settling. If it is missing or unavailable, it does not replace it with the latest event. A multi-event review's summary is not bound to its first detection or shared as a single-event analysis.

## Conversation and feedback

`sentient_security_event` lists candidate events, reads their evidence and alert reasons, or fetches current occupancy sensor reports. The selected ID is included as a bounded reference in subsequent prompts for that conversation. Missing/ambiguous selection asks which event; current sensor reports cannot establish that the same person stayed or left. Historical summaries are not live evidence. An explicit question about the latest alert can select a unique alert in the last five minutes; competing alerts within five seconds require clarification.

Camera and sensor exposure and read permissions are checked, including after asynchronous permission lookups. Settings/privacy/guest changes invalidate references and late reads. Closing or changing the owner/conversation prevents reuse. These are Home Assistant conversation, device and user identifiers, not biometric speaker identification. A standard agent needs a current chat log, or the Sentient agent's current session, to carry the reference safely.

`sentient_security_feedback` accepts an admin's explicit event correction; outside-data/action safeguards remain in force. The Mind page also offers admin feedback buttons beside individual events. They show pending saves, acknowledged results, errors and clear actions. Existing cached clip answers surface the event's latest enabled household correction alongside the original inference.

Feedback never changes Frigate thresholds, masks, zones, alarm state or announcement suppression automatically. The camera summary counts only reviewed events, rather than claiming a measured false-positive rate. Three reviewed person events marked cat/false positive produce a suggestion to review that camera's detection settings. Feedback remains a household report, separately labelled from detector output and AI inference.

## Shared analysis and cost

The shared cache coalesces concurrent compatible requests and reuses completed results. Keys include camera, event ID, footage range, provider, identity profile and the current settings/privacy context. Both compatible paths use the same neutral structured analysis and at most the first 30 seconds of a finished event. Budget is spent inside the producer, once, rather than by each waiter. Individual waiter cancellation does not cancel another valid waiter's producer.

Custom questions, different providers, identity-assisted analyses and different footage ranges remain separate. A review containing several detections cannot be reused as analysis of its first event. Existing Frigate-generated review summaries still use their existing no-additional-AI path. The feature does not poll an AI or create a new background analysis schedule.

## Retention and disabling

- At most 200 event metadata/summary/feedback records are kept locally, based on a seven-day event window. Local expiry maintenance runs every six hours, including for retained records after the features are switched off. Frigate remains responsible for its own media retention; links may expire earlier.
- At most 100 shared results are retained in memory for six hours, with at most eight concurrent shared producers. They are not restored after a restart.
- At most 100 conversation references are kept in memory for five minutes and are never restored after a restart.
- Disabling a feature stops its optional matching, reference reuse, feedback or shared-cache work and hides its relevant controls/tools. Event records remain until expiry or explicit forgetting. Settings changes clear references and the shared cache. Subscription changes and unload invalidate late work.
- Forget recent data clears shared results/references and removes event records with recent detections, alert reasons, analyses or feedback. Late asynchronous work cannot repopulate them. This does not delete recordings from Frigate itself.
- Already-dispatched provider requests or phone notifications cannot be recalled. The code does not claim independent physical verification from a device report or AI summary.

Validation: **1,531 Python tests** and **9 JavaScript tests** pass, including **64 new Python regression cases** and **3 new frontend cases**. Compilation, frontend syntax, whitespace and English translation parity checks pass. Hardware testing with real Frigate MQTT, cameras, speakers and the configured AI provider remains necessary after installation.
