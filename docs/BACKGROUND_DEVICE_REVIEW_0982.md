# Background device reliability fixes in 0.98.2

This follow-on review covers auto-off dispatch, rain forecast waits and shared camera wake work. It uses existing feature/rule settings and adds no new optional feature or AI call.

## Auto-off

Previously, unavailable or missing motion sensors were removed from the calculation. Remaining clear sensors could therefore make an uncertain room look empty. All configured motion sensors must now explicitly report `off` before the motion-based timeout can fire. An independently configured “on for N minutes” limit still applies as requested.

A queued batch rechecks the live rule, targets, motion/time conditions, light-show exclusions and never-touch list immediately before each dispatch. Deleted/edited rules and unloaded work stop. Overlapping batches do not send duplicate in-flight requests to the same entity. Each service call has a ten-second deadline.

Only accepted service requests enter the auto-off return/recent-action record. Failed, timed-out and blocked devices are not described as switched off. Timeline wording distinguishes a switch-off request from physical success, and a configured completion sentence is spoken only if all requested targets were accepted and report an off/resting state. Learning that someone turned a device back on requires a real resting-to-on transition, not a brightness/attribute update or reconnection.

The existing manual Settings test can bypass the timer conditions because it is an explicit request; it still respects the never-touch list, current targets and lifecycle. The existing websocket result key `switched_off` contains accepted requests and includes an uncertainty note. A normal later rule evaluation may still request off again if the device remains on and due; uncertain calls are not immediately retried.

## Rain warnings

A forecast request is bounded to ten seconds, and concurrent checks share the engine's “already running” guard rather than issue another forecast call. After a qualifying forecast returns, settings/lifecycle and current open windows are rechecked. Closing a window, disabling warnings or unloading while the provider is answering prevents a stale announcement.

Forecast payload shape, timezone-aware timestamps and finite 0–100 probabilities are checked. Numeric probability strings are supported; malformed rows, past forecasts and forecasts beyond the next hour are skipped. No qualifying usable row means no warning, not proof of dry weather. Existing cooldown and speech rules still apply, and the explicit manual rain test remains available.

## Camera wake-up

Previously, a camera was marked recently woken before its wake actions or settling delay completed. A simultaneous request could skip the wait, and a failed wake could prevent the next explicit request from trying again for a minute.

Wake work is now scoped to the Home Assistant runtime and shared per camera. Concurrent requests wait for the same actions and settling delay. Only completed wake operations are cached, for 60 seconds; the cache is limited to 64 camera entries. Changing the configured actions/delay invalidates reuse. One cancelled caller does not cancel a shared wake another caller needs. Settings changes and integration unload cancel owned wake jobs and clear cached completions.

A required wake has a 30-second deadline covering actions and settling. Failure or timeout reports unconfirmed image freshness instead of analysing a potentially stale image. A later explicit request can try again; there is no automatic wake retry. Camera-security descriptions fall back to the plain sensor alert if waking fails, so an unchecked image cannot dismiss the alert. Completing wake actions does not independently prove the camera image is fresh or accurate.

Real-house checks after installation: restore a motion sensor during an auto-off delay, change occupancy while a multi-device rule runs, close a window while the forecast is loading, and issue simultaneous looks at a configured sleepy camera. Confirm normal rule behaviour and manual tests still match the household's intended settings.

Validation: 1,010 Python tests passed, including 44 new regression cases; all six frontend tests passed. Python compilation, English strings/translation equality and `git diff --check` also passed locally. Real hardware timing and camera freshness still require installation testing.
