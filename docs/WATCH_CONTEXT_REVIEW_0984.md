# Watch results and wake-word context review — 0.98.4

## Automatic conversation continuation

The existing optional carry-on setting can reuse a recent conversation after a fresh wake word. Its lookup now uses the exact Home Assistant device, satellite and user combination, rather than the speaker alone. A request attributed to another user or another device does not automatically inherit that conversation ID.

Automatic continuation records are invalidated by changed settings, forgetting recent data, or entering guest mode. Guest requests neither use nor seed that lookup. A delayed reply cannot seed it after those changes, including entering and leaving guest mode while the request was running. Expired records are pruned and the lookup is bounded to 100 owners.

This scopes automatic reuse using Home Assistant's metadata. It does not identify voices or distinguish two people who share the same anonymous satellite identity. It also does not erase chat history held by a provider or replace a conversation ID explicitly supplied by a client. Existing listening and discussion rules remain in place.

## Temporary camera watches

A positive answer marked unsure can no longer trigger an alert. If the configured second opinion is available within the existing budget, its final answer must be certain too. Otherwise the result stays unknown and the frame remains eligible for a later check after cooldown.

Structured results require real boolean answer/unsure fields and a text or absent description. Malformed results are reported as errors, rather than becoming positives, false all-clears or uncaught type errors. For legacy unstructured providers only an exact yes/no/true/false answer is accepted; qualified prose such as “yes, but it is unclear” is unknown/error.

Only successfully evaluated frames remain in the unchanged-image cache. Preparation failures, AI failures, malformed/uncertain results, budget races and cancellation release that fingerprint, so a recovery can evaluate the same picture again. Busy slots are released on cancellation as well as success and error. An in-progress check shows unknown rather than an old observation.

Camera watches check the existing quota before fingerprint/image work, prepare their attachment before spending a job, and recheck the quota after preparation. Second opinions reuse the prepared attachment reference. A media-source reference may resolve to a newer live image; this is not a frozen-snapshot guarantee.

## Queued state alerts and reminders

Before a queued watch alert fires it rechecks that the manager is running, the exact watch remains registered, and a state/camera watch has not expired. Cancelled, replaced, stopped and expired work cannot announce a match. Reminders retain their separate due-time behaviour: reaching their deadline is the reason to speak, so this guard does not suppress them.

These are reliability fixes to existing controls. No new optional feature, setting or additional AI call is introduced. Live testing should cover an unclear image with/without a second-opinion provider, recovery after a provider error, and wake-word continuation across guest mode or a settings change.

## Validation

All **1,118 Python tests** pass, including **50 new regression cases** across `test_watch_result_reliability.py` and `test_carry_context_isolation.py`. All **6 JavaScript tests** pass. Patch whitespace, Python compilation, frontend syntax and identical English translation files were checked. Live Home Assistant speakers/cameras and provider outages were not exercised in this review.
