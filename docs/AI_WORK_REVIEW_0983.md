# AI work and saved-answer reliability in 0.98.3

## AI gateway

The daily allowance continues to count logical jobs. The meter also shows recorded `ai_task.generate_data` service attempts and retry attempts. Each of Sentient's attempts is now included in the existing metadata-only privacy log when that log is enabled. These are service submissions observed by Sentient, not provider billing, confirmed network delivery or retries performed inside a provider integration. Historical attempts from older versions cannot be reconstructed.

Settings changes, forgetting sent records and integration unload invalidate pending retries. A request started under an old configuration cannot reset or increment current provider-health status after that configuration changes. Changing the configured AI task entity clears its old consecutive-failure/error/last-success state, while retaining daily job/failure totals. Success metadata is saved even when there was no earlier failure. Invalid service response formats use the normal unavailable/fallback path.

Saved send metadata is trimmed to its existing 14-day retention window on loading, and is cleared rather than restored when Privacy log is off. Attempt counters persist and reset at the local day boundary. There is still only one existing busy retry and the existing overall timeout; this patch adds no backoff feature or new AI calls.

## Standing camera checks

Checks now reserve their busy slot before any fingerprint or wake await, preventing overlapping triggers from spending twice. Current rule identity/enabled state, configuration, privacy, guest mode, lifecycle and camera availability are rechecked after preparation and after the AI returns. Disabled, deleted, edited or invalidated work cannot publish or speak a late positive result. The busy slot is released on success, failure or cancellation.

Image preparation happens before charging the AI-job budget. A preparation failure therefore cannot consume a job without submitting AI work. A new attempt records an unknown result until a valid typed response arrives. `match` must be a real boolean and description must be text (or absent); string “false” and malformed data cannot become a positive alert or a false all-clear. Non-finite object-count readings do not trigger a check. An explicit manual test retains its existing cooldown/disabled-rule override, while still checking identity and lifecycle.

## Saved offer answers

The deterministic speaker/phone answer callback is bound to the exact offer configuration that was asked, has a two-minute deadline and is consumed once. Editing actions, disabling/deleting the offer, entering guest mode, changing settings/privacy or unloading prevents that old callback from acting. Checks run again after asynchronous action validation and immediately before starting the configured script. A never-touch rejection and an empty action list no longer return a misleading “Done”.

Direct explicit offer-tool requests use the current enabled offer and the same pre-execution checks. The explicit Settings test may still try a disabled rule because the administrator requested that test. Starting a configured script is not independent physical verification, and already-dispatched actions cannot be recalled or silently reversed by changing settings.

These fixes use existing feature controls. No additional optional behaviour or switches are introduced. Live testing should include a provider failure while changing its setting, a camera check disabled while its answer is delayed, and an offer edited between its question and a spoken/phone “yes”.

Camera prefilter keys include the camera, spot and question. A failed/invalid evaluation drops its fingerprint so a later eligible check can try again after recovery; changing the question cannot reuse the old question's decision. Fingerprint storage is bounded to 200 entries. Forgetting clears transient image-answer/fingerprint caches and camera-watch/standing-check descriptions in the selected recent window, preserving their configured questions and rules.

Camera preparation is skipped when the existing AI budget already blocks the job. Direct budget queries roll at midnight. Settings changes/unload invalidate camera caches; delayed on-demand looks, fingerprints and camera-watch replies cannot refill them or fire an expired/forgotten watch after invalidation.

## Validation

The complete Python suite passes: **1,068 tests**, including **58 new regression cases** in `test_ai_work_lifecycle.py`. All **6 JavaScript tests** pass. Frontend syntax, patch whitespace and identical English translation files were checked. Live speakers, cameras and provider failures were not exercised against a running Home Assistant installation in this review.
