# Conversation sessions, guarded control and security evidence

These additions are opt-in. Existing Assist + Sentient agents keep their current behaviour unless you enable them. No realtime API is required: your existing speech recognition, conversation model and TTS are used. The control checks, sessions, undo and incident grouping do not make their own AI calls.

## Set up conversation sessions

1. In Sentient Settings → Voice backup, select your main AI conversation agent.
2. Point the Home Assistant Assist pipeline at **<persona> with backup**.
3. In Sentient Settings → Voice, enable **Conversation sessions**. Start with the 60-second inactivity timeout.
4. Ask for information, security, health or system status, or say “let's talk” or “help me sort the heating”. The voice keeps the same topic, selected devices and unanswered question across turns. “And upstairs?” is interpreted with that context; ambiguous targets should still be clarified.
5. A completed single command stops listening unless **Sentient's spoken reply ends in a question**. This is based on the reply, not a question mark in your request or a provider's keep-listening flag. Clarification and confirmation questions reopen listening.
6. Information, security, health and system discussions stay open. After an answer, Sentient asks “Is there anything else you'd like to know?” unless its answer already ends in a question. Once a discussion is active, a command within it does not end the discussion.
7. Say “that's all”, “that is all”, “enough”, “that's enough”, “stop listening”, “stop”, “nothing else” or “no thanks” to finish. These must be standalone endings: “stop the vacuum” remains a device command. Ending cancels pending actions. Errors and empty replies do not reopen listening.

Clear commands and information questions are routed locally; less obvious wording can be classified through the existing model's session tool. There is no extra classification model call. The session tracks its goal, selected entities, missing details and task state. Clarification replies keep the previous goal; the model can refine it for corrections. Task completion does not close an active discussion. Target interpretation and correction quality still depend on the conversation model, and expired confirmations are never restored.

Follow-up listening uses Home Assistant's `continue_conversation` signal. Your satellite/pipeline must support it. The physical microphone's silence timeout is controlled by the device; Sentient does not keep it permanently open. Session context expires lazily when inspected or used after the configured timeout. Restarting or reloading Sentient discards sessions and pending actions. Expiry does not delete your underlying AI agent's own chat history.

Managed sessions use completed responses rather than streaming so Sentient can apply the final listening decision and replace an unsafe model confirmation before it is spoken. This may increase perceived response time compared with streaming.

The Mind section shows the current goal, unanswered question, missing details, why listening continued or stopped, and whether undo is available. Sessions are isolated by conversation ID, device, satellite and Home Assistant user; there is no automatic hand-off to a different speaker. Voice recognition is not speaker identification.

## Enable enforced confirmation

Use a **dedicated AI conversation agent** if you want to retain another agent's unrestricted routines and scenes.

1. Configure an OpenAI Conversation, Google Generative AI Conversation, or Ollama conversation agent.
2. Under that agent's **Control Home Assistant**, select **only Sentient guarded**. Remove both **Assist** and the ordinary **Sentient** API from this agent. Native device-control APIs would bypass this guard.
3. Choose that agent as Sentient's main voice agent, keep the pipeline on **<persona> with backup**, turn off the pipeline's **Prefer handling commands locally**, and enable **Enforced voice confirmation** under Sentient Settings → Voice.
4. Expose the intended entities to Assist in Home Assistant. Guarded actions also check the Home Assistant user's entity-control permission when a user ID is present.
5. Try an exposed light first. For a lock or cover, Sentient reads the exact requested action and waits for a separate “yes”, “yes please”, “confirm”, “go ahead” or “do it”. “No” or another request discards the pending action.

The guard is enforced in code: a model-supplied `confirmed: true` cannot authorise anything. A pending batch expires after 60 seconds (or earlier when the session expires), belongs to that conversation/speaker/user, and is consumed before execution. The device state and permissions are checked again. If a device changes while waiting, ask again. A new request cannot silently replace a pending batch.

Always-confirm actions are locks, opening/closing covers, starting a vacuum, turning on a switch, and raising a thermostat more than 2°C/3.6°F or above 22°C/71.6°F. Light changes, fan on/off, stop/off actions and smaller heating adjustments run without confirmation. The ordinary “Ask shall I before” setting remains an AI instruction on the original API; the guarded API has the minimum checks above regardless of that setting.

Guarded control deliberately supports explicit light, switch, fan, lock, cover, thermostat and vacuum operations. It does not execute scenes, scripts, alarm arming/disarming, arbitrary services, adopted routines or rule creation. Scene-like lighting can be expressed as explicit light settings, which are eligible for undo. Memory, questions, reminders, watches, briefings and incident queries remain available. Some other device types are not supported yet.

A wrong API selection or an unsupported agent fails closed with setup instructions. In guarded mode, an AI error or timeout **never replays the request on the backup agent**: a partly completed action must not run twice. Already completed actions are not automatically rolled back. State reports are checked briefly after each call and labelled **verified**, **attempted** or **failed**. “Verified” means the device reported the target state, not independent physical proof. Home Assistant can handle custom sentence-trigger automations before calling the conversation agent, even with local intent preference off. Remove any conflicting sentence triggers for guarded actions. These triggers, other pipelines and existing automations remain outside this guard; Sentient cannot intercept requests that never reach its agent.

## Undo light changes

Enable **Undo recent light changes** under Voice, with guarded control enabled. Say “undo that”, “undo what you just did” or “put it back”.

The most recent action turn's verified lights are restored to their previous on/off state, brightness and supported colour setting. Changes from multiple control calls in that turn are grouped. Undo is scoped to the same conversation/speaker/user, lasts at most five minutes and also expires with the shorter session timeout. Devices changed since the action are skipped; exposure and permissions are rechecked. Locks, covers, heating and appliances are never rolled back. A restore request is reported as attempted rather than claiming unverified success.

## Evidence and uncertainty

Enable **Explain evidence and uncertainty** under Voice. The prompt tells the model to separate observations, inferences, attempted actions and verified reports. Guarded device queries provide report timestamps, ages and unknown/unavailable states. Security checks include the last usable reading cached locally for up to 1,000 security entities, including after a restart.

An unavailable lock or opening sensor prevents an “everything is secure” answer even when the option is off. A house with no security sensors is also unknown. An old but unchanged event-driven contact sensor is not automatically labelled stale just because it has been quiet. The stored last-known reading never overrides a current unknown state.

## Group security incidents

Enable **Group security incidents** in Sentient Settings → Safety & security. Select entry-contact entities and person sensors in Camera security; enable Camera security itself for alerts. The incident collector groups those sensor changes, camera-security alerts and available clip summaries. It does not analyse new video or identify people by itself.

Related events within three minutes are put in one episode. This is a possible temporal link, **not proof of the same person**. Ambiguous matches are kept separate; delayed clips must match a known camera and the original event time. All evidence is retained even when a repeated alert is suppressed. Alerts repeat only for escalation from night detection to an unconfirmed entry route or loitering, or for a new incident. Smoke, CO and other critical alerts are unaffected.

The Mind section shows recent incident evidence. Ask “what happened after the gate opened?” or “what happened in the garden?”; the `sentient_incidents` tool returns the grouped records and uncertainty. Records are stored locally, retained for seven days and capped at 100 incidents with 50 evidence entries each. Switching the feature off stops new collection and hides its results; stored records age out when next loaded or accessed.

## Validation limits

Automated tests cover session isolation/expiry, separate-turn confirmation, exact pending batches, permissions, changed devices, cancellation, failure without fallback, safe undo, unavailable readings, incident correlation, escalation and retention. Live microphones, STT accuracy, TTS latency and physical device behaviour still need checking on your installation before relying on them.

## Conversation replay checks

Run `pytest -q tests/test_conversation_replay.py` to replay commands, clarification replies, information discussions, topic changes and explicit endings without a microphone or an extra model call. The assertions check the spoken follow-up, retained goal, task state and listening decision. These deterministic tests cover the controller policy, not live model interpretation or microphone behaviour.
