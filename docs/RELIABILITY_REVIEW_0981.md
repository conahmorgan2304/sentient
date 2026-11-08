# Sentient 0.98.1 reliability review

Reviewed the current repository's conversation/session and guarded-control paths, speech and speaker checks, privacy/forgetting, current-house context, AI gateway and language caches, numeric sensor consumers, safety-alert repeats, Settings/websocket paths, backups and release validation. The baseline suite passed 935 Python tests and six frontend tests. This is a code and automated-test review; live Home Assistant, Echo/ESPHome audio, real camera streams and power-loss behaviour require installation testing.

## Fixes

| Finding | Result |
| --- | --- |
| Non-finite sensor strings such as `NaN` could satisfy numeric watches and offer conditions. Invalid battery values could crash health checks while rounding. | Shared finite-number parsing rejects these readings; battery percentages outside 0–100 are also ignored. Heating statistics and numeric noticing use the same parser. Invalid numeric transitions do not consume noticing calls. |
| A delayed noticing reply could speak after guest mode, Settings changes or forgetting. Forgotten noticing decisions/pending changes were retained. | Rules and privacy generation are checked after the AI reply; recent noticing records are cleared when forgetting. Replies and failures from invalidated/unloaded work cannot republish their observation text. Direct automation remarks also respect guest mode. |
| Malformed AI results could treat string `"false"` as true, open a question, or crash on a non-text message. | Noticing requires actual boolean flags and a non-empty text message. |
| Safety-alert repeat callbacks did not recheck the feature switch or ignore list. | Repeats stop when the feature is disabled or the sensor is ignored. The explicit manual safety test remains available. |
| Exposure lookup errors implicitly allowed entities. | Voice tools deny access when exposure cannot be established. Two tests now explicitly initialise/configure exposure rather than depend on missing exposure data. |
| One speaker exception could abort the setup test. Missing speakers were silently omitted and an empty list appeared successful. | Per-speaker errors and 30-second timeouts are reported, the remaining destinations are checked, missing speakers are listed, and an empty list is shown as unconfigured. Service acceptance remains distinct from audibility. No automatic retry. |
| Backup files were written directly over their final filename. | Writes use a private temporary file, flush/sync, then atomic replacement. Failed replacement preserves the previous file and cleans the temporary file. |

These are reliability fixes using existing controls; they add no background AI calls and require no new settings.

## Follow-on improvements

1. Show feature readiness and missing dependencies together in Settings. Optional conversation features now span several switches and agent/API requirements; a configured switch alone does not establish an end-to-end working voice pipeline.
2. Consider shared, bounded backoff during AI-provider outages. The gateway already has timeouts and one busy retry, but separate background jobs can still reach an unavailable provider. Explicit user requests and urgent work need distinct handling. Show provider retries separately from logical AI jobs to make the meter clearer.
3. Expand cheap conversational intent recognition beyond English. Speech translation and prepared endings already help, while corrections, recovery and direct explanation phrases still have English-specific rules.
4. Run a repeatable real-house check after installation: an ordinary command closes, an information question follows the configured policy, “no thank you” ends it, a speaker failure remains a delivery problem, and a late device response never causes an automatic replay. Check backup restore on a test installation.

The existing suite also exercises language fallback/caching, memory/topic privacy, permission revocation, asynchronous confirmation expiry, partial delivery and current-house context. Passing checks establish the tested paths, not a guarantee that every hardware/integration combination is fault-free.

A further validation improvement is a Home Assistant compatibility matrix in CI. The current workflow uses one pinned test environment; it should also exercise the versions the integration intends to support.

## Final validation

966 Python tests passed, including 31 new review regression cases; all six frontend tests passed. Python compilation, panel JavaScript syntax, `git diff --check` and English strings/translation equality also passed locally. GitHub HACS and hassfest validation is checked on the pull request.
