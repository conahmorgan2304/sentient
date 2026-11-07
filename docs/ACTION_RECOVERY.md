# Device results and cautious recovery — 0.94.0

Enable **Settings → Voice → Device results and cautious recovery**, alongside **Conversation sessions**. Use the **Sentient with backup** agent configured with **Sentient guarded** for recorded device actions and enforced retry controls. This new option is off by default.

## Clearer partial results

After guarded device actions, the final reply names the devices and distinguishes:

- **Reported the requested state:** Home Assistant reported the target state. This is not independent proof of the physical result.
- **Request sent, result not confirmed:** the command was dispatched but its outcome is uncertain.
- **Request failed:** delivery may still be uncertain if the service call had begun.
- **Not sent:** the batch stopped before that device's action was dispatched.

With this option enabled, known guarded outcomes replace a model's generic “done” claim. Earlier correction notices are retained. Multiple guarded batches in the same turn are combined, keeping the latest action per device up to 20 devices. Existing single-command/discussion listening rules still apply; a partial result alone does not add an unnecessary “anything else?” question.

## “That didn't work”

Say **“that didn't work”**, **“did that work?”**, **“what failed?”** or **“retry the failed devices”**. Sentient checks the last recorded guarded request against fresh Home Assistant states without an AI classification call.

A fresh wake-word conversation on the same reported device/satellite/user scope can inspect the record. It lasts at most two minutes and is superseded by a new ordinary request. A different speaker or user cannot use it. Fully unidentified requests have no recovery record. This uses Home Assistant's reported identity, not biometric voice identification.

The response distinguishes a delayed success, an earlier successful state that has since changed, uncertain dispatch and actions that never started. Devices no longer accessible are not named. A state change after earlier success is not overwritten.

## Continuing an interrupted batch

Only actions known **never to have been dispatched** can be offered again. Sentient names those actions and asks a new confirmation question. A separate affirmative turn is required even for an ordinary light command. Current permissions, exposure, never-touch rules, supported values and device snapshots are checked again before dispatch. If a device changes while waiting for confirmation, the batch is refused and the user must ask again.

Already successful actions, delayed successes and uncertain dispatched actions are not replayed. A timeout or service exception does not prove that nothing happened. To make another attempt at an uncertain device, inspect it and give a fresh explicit device request; the recovery handler does not silently repeat it.

Recovery applies to explicit guarded device actions. It does not reconstruct plans from free-form conversation, native provider tools, scripts, scenes or Echo announcement failures. Without a suitable record, Sentient says so and requests the device and intended result rather than guessing. Existing speaker delivery checks remain the way to investigate audibility.

## Privacy and limits

Records are memory-only and bounded to 20 speaker/user scopes, with at most 20 latest device actions per record. They contain explicit action data, dispatch/result evidence and expiry, not a saved permission to act. New Settings, forgetting recent data and unload clear them. Guest mode does not expose or retain them. Old in-flight/queued batches cannot repopulate records after a clear or replace the record of a newer request.

Disabling the option clears records and stops deterministic recovery and detailed final-outcome replies. It does not cancel or undo a request already sent. The recovery phrases are currently exact English phrases; speech-language support can translate the replies but is not full multilingual recovery-intent recognition.

Tests use Home Assistant service/provider doubles for partial failures, delayed states, changed permissions, fresh confirmation, scope isolation, expiry and in-flight races. Live device reports and physical audibility still need installation testing.
