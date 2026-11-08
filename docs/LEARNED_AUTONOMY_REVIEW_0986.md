# Learned autonomy review — 0.98.6

This release improves existing learned routines, suggestions and trust controls. No additional AI call or new autonomous action policy is introduced. Existing Adopt/Stop, Suggest routines, Learn entities, anchor/grouping and per-kind Trust settings remain in charge.

## Keep the agreement stable

Nightly relearning previously copied an adopted flag onto a fresh timing estimate. That could move an approved clock routine or change it into an anchored habit without another agreement. An adopted routine/link now retains its approved object and evidence while new estimates continue for unadopted candidates. An approved sunset/sunrise/getting-up rule still follows its original anchor and offset as the day changes; its displayed clock time may move normally. Stop adoption before relearning if a new timing estimate is wanted.

Saved deterministic suggestion answers are bound to the exact currently registered group, its action/timing configuration, settings and privacy/guest generation. They expire after two minutes and are consumed once. If a member changes or is removed, the group is not partly adopted. Changed/expired answers do not alter routines or feedback. Concurrent suggestion attempts do not ask twice, and the existing daily suggestion allowance is checked inside the background entry point too.

These checks protect Sentient's deterministic speaker/phone callbacks. Explicit tools/services still operate on the current routine the user requests; this does not add voice biometrics or a new authentication system.

## Recheck before dispatch

A learned action must still be a registered adopted routine, with the same approved plan and settings. Missing/unknown/unavailable targets, changed privacy/lifecycle, another feature taking over the target, and never-touch entries block submission. Checks run again after asynchronous speech/confirmation, immediately before the service call. A stale queued reference cannot act after the routine has been stopped or deleted.

Shared Trust callbacks are once-only, expire after two minutes, and check their original options, entity state/attributes, caller precondition and privacy/lifecycle. Tell-then-do rechecks after speech as well. Locks/alarms cannot execute through this background trust path; gate/garage/door covers retain their existing always-ask rule. Concurrent submissions to the same entity are guarded. Trust success history is not restored by a late completion after a policy/privacy change.

An accepted deterministic trust action says “Request sent.” It is a service submission, not physical verification; callers using the older no-return callback contract retain that contract. Already-dispatched device actions cannot be recalled by a later setting change.

The minute scheduler records its last dispatch per routine/local date/minute, preventing repeated ticks (including a repeated local time at the autumn clock change) from dispatching it twice. This small ledger is in memory; it is not a persistent exactly-once guarantee across restarts.

## Learn from stronger evidence

Anchor confidence now uses all eligible days when the anchor is known, including days with no matching action. A few matches cannot inflate themselves to 100% by discarding the other days. Recovery through unknown/unavailable breaks a change sequence rather than being counted as a deliberate human action. Link eligibility respects an unknown current effect state, and future-dated changes are excluded.

Only one history-learning pass runs at a time. Routine and link replacements are staged together and committed only if settings, privacy and the user's current decisions remain unchanged across recorder/logbook awaits. Dismissed, newly adopted or stopped work cannot be overwritten by a late pass. With no selected effect entities, it no longer reads unrelated door histories for link analysis.

Live testing should cover accepting a grouped suggestion, declining/deleting one while its question is open, changing the never-touch list during tell-then-do speech, and reviewing an accepted sunset routine after another nightly learning pass.

## Validation

All **1,229 Python tests** pass, including **52 new regression cases** in `test_learned_autonomy_reliability.py`. All **6 JavaScript tests** pass. Python compilation, frontend syntax, whitespace and English translation parity were checked. Older private-helper fixtures now register the real adopted routine, and the repeated suggestion-style test explicitly resets its daily allowance. Live Home Assistant device/voice testing remains after installation.
