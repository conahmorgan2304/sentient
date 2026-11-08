# Cross-system review — 0.98.8

Reviewed against merged main after 0.98.7. The pass combined the complete regression suite and syntax/JSON checks with source review of conversation/session ownership, recent heating/trust changes, settings and websocket entry points, persistence/restore, proposals, goals and background summaries. This is a broad reliability review, not a claim that every device/provider behaviour or every possible fault has been exercised.

| Finding | Change |
| --- | --- |
| Import could overwrite current named routines and camera spots despite its additive wording | Existing canonical names are preserved; only missing records are added |
| Spot aliases and polygon masks were lost on restore | New spot restores include aliases, polygon points and box geometry |
| Restore could reapply an old adoption flag to a newly estimated routine with the same ID | Exports include its semantic plan; adoption requires an exact plan match and real boolean decisions |
| Old deletion records could remove a current learned routine | Current routines/links are preserved |
| Imported memory bypassed capacity and could collide with an existing ID or contain invalid metadata | Validate metadata, preserve categories, reject ID collisions, respect capacity without evicting existing/pinned facts |
| Malformed later sections/settings could fail after earlier stores had already changed | Structural and supported-settings preflight runs before mutation; invalid individual new records are skipped and counted |
| Fuzzy lookup suppressed distinct named list items; repeated mutes/rules could duplicate | Restore compares canonical/exact names and updates duplicate indexes as it adds |
| Two same-minute backups could overwrite the same file | Unique names, one writer at a time, stable copied export snapshots and retention that keeps the newly written file |
| A backup filename could resolve through a symlink outside the backup folder | Restore rejects an external resolved path |
| Disabled proposals could still be accepted | Direct answer path enforces the toggle; stale, expired and invalid changes are refused |
| A suggested increase from daily AI limit 1 could round down to 0, meaning unlimited | Increases must be strictly positive and bounded within the existing UI maximum |
| Clearing a customised setting could allow an old proposal to overwrite its effective default | Compare the current effective setting, including defaults, to the original proposal |
| Non-finite goal readings could poison baselines/progress or crash rounding | Ignore invalid readings and reject non-finite progress calculations |
| Delayed goal work could exceed the open-goal limit or update closed/disabled/reconfigured work | Recheck after awaits, enforce the limit again and serialise checks |
| Empty/ambiguous close-goal text could close the first unrelated match | Require a unique exact or partial match |
| A delayed digest/briefing could republish forgotten or reconfigured material | Check settings, privacy/guest generation and lifecycle before publishing/speaking |
| A briefing could consume newer thoughts it never included | Remove only the exact queued thought IDs selected for that briefing |

## Restore and backup compatibility

The JSON format marker remains 1. Older ordinary records can still be added. Existing routines, spots and named-list entries are not overwritten; restore is not a replacement/editing workflow. Shared motion-light night hours change only when the explicit settings-restore option is selected.

Legacy learned-choice records that contain only an ID cannot prove which plan was accepted, so they do not automatically adopt/decline a current plan. Re-approve the current routine explicitly if needed. New exports include the plan (with the adopted/declined flags kept separate), preserving approved anchors while allowing their displayed clock time to follow the day.

Supported imported settings are checked using the current selectors, with strict boolean/finite-number types; optional null selections remain supported. Unknown settings from old/other versions are ignored. A settings restore is refused if settings changed while its file was being read. Individual invalid/colliding/capacity-blocked records are reported as skipped. Preflight prevents malformed section/settings failures before mutation; this is not a transactional rollback system for arbitrary internal errors.

Backup saves are serialised. Automatic queued saves recheck the existing toggle and lifecycle, while an explicit manual save still works when the automatic schedule is off. Already-running local file writes may complete after a settings change. Backup archives remain separate from “forget recent”; they are not silently deleted by that command. Retention preserves the newly written file and uses file modification order for earlier copies.

## Proposals, goals and summaries

Settings proposals expire after seven days. Declined-proposal suppression retains its existing thirty-day behaviour. Supported transition/value checks apply before a yes is recorded or settings change. No proposal applies without an explicit answer; no new setting or autonomous permission is introduced.

Goals retain their existing optional toggle. Creation/checks stop or discard late work after disablement, settings/privacy changes or unload. Closing/removing/replacing a goal while its history is loading prevents that result being appended. Concurrent creation cannot exceed the existing open-goal cap. These fixes leave the existing daily-history aggregation method in place: resetting cumulative meters and time-weighted daily sensor averages remain an area for a deeper statistics review; progress is still a history-derived estimate.

Briefings and digests retain their existing feature/schedule controls and AI gateway. Invalidated summaries are discarded before speaking/publishing; new queued thoughts are not consumed by an earlier briefing. Already-dispatched speech cannot be recalled. No additional AI call is added by this release.

## Review limits and live checks

Existing admin checks on backup/import and settings-changing websocket operations were inspected; this pass does not introduce a new access-control policy for the shared household page. Real voice pipelines, cameras, thermostats, provider failures and a running household were not available here. Live validation should include exporting/restoring into a test entry, cancelling a settings proposal after changing its option, disabling a goal during a recorder read, and forgetting while a digest/briefing is pending.

## Validation

All **1,331 Python tests** pass, including **58 new regression cases** in `test_full_review_0988.py`. All **6 JavaScript tests** pass. Python compilation, frontend syntax, patch whitespace, identical English translation files and parsing all five integration JSON files were checked.
