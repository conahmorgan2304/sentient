# Camera and security reliability — 0.98.11

This release strengthens existing camera security, automatic clip watching and event-clip questions. It introduces no new AI service, model, automation or optional feature.

## More faithful evidence

- A current snapshot showing nobody no longer dismisses an earlier person-sensor detection. The alert records that the later image did not verify the detection. Invalid AI person values are unknown, rather than a negative result; descriptions are accepted only with an actual boolean positive result.
- Camera matching uses a unique device match or a unique full normalised name. A first-word prefix cannot pick a neighbouring camera. Ambiguous views leave the original sensor alert intact without a speculative image check.
- Boundary reasoning requires every configured inside sensor to have been known off for ten minutes and every configured entry contact to be known closed with no recent state change. Startup, missing/unknown contacts, unconfigured entries and ongoing occupancy do not establish that history.
- Alerts say that no recent entry opening was recorded or no recent indoor activity was detected. They do not claim forced entry or that everyone is asleep. Cross-camera detections are always labelled a possible route, regardless of optional incident/evidence settings.

## Current work only

- Pending security alerts, journeys and loitering timers are invalidated on settings changes, unload and forgetting. In-flight descriptions are also checked against the current privacy, guest and AI context before publishing. Loitering rechecks quiet hours.
- Camera alert tasks are retained and cancelled on clear/unload. Reservations are released even when a task is cancelled before its first turn. An older task cannot release a newer pending alert's key.
- Both automatic clip watching and the separate event-clip path recheck their context after asynchronous preparation and analysis. A delayed post-alert review checks its originating context before and after waiting for the recording. Stale results cannot restore last answers, incidents or timeline summaries.
- Queued clip notifications recheck context before each phone. Already-dispatched notifications or provider requests cannot be recalled.

## Less duplicate work and clearer diagnosis

- Only one review per camera can be in flight in each clip subsystem, including forced requests. Automatic reviews already present in retained summaries are not analysed again after the cooldown. Existing cached summaries still answer camera questions without another AI call.
- Clip files have unique names so an old runtime cannot overwrite or delete a new runtime's media. Automatic clip cleanup includes cancellation during media preparation. The keep-media setting remains in use; stale media is removed.
- Clip worth/not-worth values require actual booleans. Malformed/non-finite clip times are rejected before spending AI allowance; invalid Frigate metadata cannot manufacture a threat level or crash the normal review path.
- Downloadable diagnostics include unresolved sensor-to-camera matches, active automatic clip work, retained-summary counts and skip reasons. They include neither clip descriptions nor recognised people's names in these new fields.

Existing feature switches control automatic work. Settings changes clear transient security observations; forgetting also clears transient journeys and invalidates pending reviews. Historical timeline/clip-summary retention still follows the existing settings and forgetting period. Manual event-clip questions retain their existing explicit-request behaviour.

## Limits and validation

The alert-description image remains a current camera snapshot, not a verified frame from the triggering event. Sensor detections and AI summaries can both be wrong. Preserving sensor alerts can expose noise previously hidden by a later empty picture; event-specific evidence and detector tuning remain valuable. Time correlation does not establish identity or a confirmed route. Duplicate work is prevented within each clip subsystem, rather than across the two independent clip services.

Validation: **1,467 Python tests** and **6 JavaScript tests** pass, including **67 new regression cases** in `test_camera_security_reliability.py`. Compilation, whitespace and English translation parity checks pass. Real camera streams, Frigate events and speakers still need installation testing.
