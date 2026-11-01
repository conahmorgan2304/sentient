# Live voice checks and delivery review — 0.66.0

## Try the live check after updating

1. Enable **Conversation sessions** and select **Sentient with backup** as the conversation agent in the voice pipeline you want to check.
2. Open Sentient's sidebar page and find **LIVE VOICE CHECK** in the mind section. An administrator can press **START**. This only arms an observer; it does not speak, change devices or make an AI call.
3. On your voice device, ask **“How is the system?”**. Sentient should answer and keep listening.
4. Say **“No thankyou.”**. Sentient should acknowledge and stop listening.
5. Inspect **Observed turns**: the question should show LISTEN and the ending STOP. Confirm whether you heard both replies using YES or NO. CLEAR removes the check.

The check observes text and results reaching Sentient's conversation agent, including the agent/device identifiers Home Assistant supplies. It does not independently measure microphone capture, audible playback or the physical device stopping. You still need to watch and listen to the device. A text interaction using those same phrases can also satisfy the agent checks. If no matching question reaches Sentient, the check expires after five minutes; verify the selected pipeline and agent. A different device/user cannot complete an already-started sequence.

Only one check is held in memory, with at most two bounded excerpts. Starting again replaces it; clearing, reloading or changing settings removes it. Guest mode blocks checks and hides their results. Results and delivery summaries are excluded from recorder history, although normal Home Assistant live sensor permissions still apply. They are not included in Sentient's downloadable diagnostics.

## Per-speaker announcement results

Recent Sentient announcements now show each destination's result in the mind section. Each speaker gets a separate service call, so one failing speaker does not conceal another's success. Possible results are accepted, failed, unavailable, unroutable and timed out. A 60-second timeout bounds each delivery attempt.

**Accepted** means Home Assistant completed the service call; it does not prove anyone heard the sound. **Timed out** means the playback outcome is unknown. There are no automatic retries. If the same announcement is requested again within two minutes, accepted destinations are skipped and other destinations may retry. A timed-out speaker might already have played it. Critical alerts retain their repeat behaviour.

Service responses include delivery details and report `reason: partial` when only some destinations accepted the request. The `speakers` list and timeline list the destinations that accepted it. Total failure raises an error and remains visible in delivery history. Existing held-response fields remain compatible. Native Assist conversation replies use Home Assistant's own reply playback path; the announcement receipts do not inspect that separate path.

## Further faults fixed

- The overnight self-test no longer expects “nothing open” when contact sensors are missing or unavailable.
- Unsettled, triggered or conflicting alarm states no longer produce an expected “disarmed” answer. These cases are skipped rather than scored against an invalid assumption.
- An answer saying “not armed” cannot pass the armed-alarm check.
- Malformed or non-finite weather temperatures no longer abort the remaining self-test questions; unavailable weather readings are skipped.

## Validation

Regression coverage includes partial delivery and retry, independent timeout handling, repeated critical alerts, live-check expiry and ownership, premature/stale hearing confirmations, empty replies, guest privacy, the websocket endpoint, and the real conversation hook closing locally. The full integration suite passes: 466 tests (18 new regression cases). All frontend JavaScript passes syntax checks, translations remain identical, and the diff passes whitespace checks. Microphones, Echo devices, TTS audio and installed Home Assistant hardware cannot be verified from this development environment.

## 0.66.1 — hearing confirmation feedback

YES and NO now show a saving state, consume the server acknowledgement immediately, and replace the question with an explicit saved confirmation. Failed requests show their error beside the live check and can be retried. Duplicate clicks are blocked while saving. New sensor results replace the temporary acknowledged display, so starting another check does not retain an earlier confirmation. Three frontend regression tests cover delayed sensor updates, YES/NO, clearing, errors and retry.

## 0.66.2 — optional diagnostic features

Under **Settings → Voice**, **Live voice check** and **Speaker delivery history** can each be enabled or disabled. Both default to enabled to preserve existing behaviour. Disabling the live check clears its current run, stops observation, blocks its API and hides the controls. Disabling delivery history clears the recent-results buffer and stops collecting it; enabling again starts a fresh history. Settings take effect immediately without restarting Home Assistant.

Conversation sessions, guarded control, undo, state evidence and security incidents already have their own settings. New optional user-facing features must likewise provide settings controls. Reliability fixes, accurate service responses, failure detection and duplicate-safe delivery remain active; the delivery-history toggle controls the optional recent-results display and buffer.
