# Reliability review — 0.65.2

This review covered the main voice/session paths, guarded execution and undo, security reporting, camera alerts and incident storage, speech delivery, backup restore, diagnostic redaction, settings update paths, and the existing release checks. It is a code and automated-test review, not a certification of every integration or physical device.

## Fixes

- **Guarded actions:** revalidate each target immediately before dispatch, including after earlier service calls yield. A confirmed batch stops if a later target changed. Undo rechecks state after permission lookup, preventing a manual change during that lookup from being overwritten. Closed or unauthorised sessions cannot continue dispatch.
- **Device reports:** missing, malformed or non-finite brightness is not treated as verification; unknown thermostat target values require confirmation.
- **Security reports:** locking/closing/arming states do not establish a secure house. Triggered and pending alarms are not described as off. An alarm alone cannot prove that doors/windows are shut. Known open doors remain visible when another sensor has no reading.
- **Speech:** selecting speakers without any usable announcement/TTS route raises a delivery failure instead of logging successful speech.
- **Backups:** routine identity uses the exact normalised canonical name during restore, never the partial-name or alias matching intended for voice requests.
- **Incidents:** duplicate event IDs are checked before ambiguous temporal grouping; delayed events remain sorted by event time; expired clips are not reintroduced.
- **Camera alerts:** rejected AI checks release their suppression reservation. A later real detection can alert; accepted alerts retain duplicate/escalation suppression. This applies with incident grouping both on and off.
- **Diagnostics:** exported personal/style notes, known-car descriptions and household face names are redacted.

## Verification and limits

New regression tests exercise asynchronous state changes, stale confirmation targets, manual changes during undo, invalid reported values, missing speech routes, alarm transitions, restore name collisions, delayed/duplicate incidents, rejected camera checks and additional redaction. All 448 tests pass, including 27 new audit regression cases. JavaScript syntax, English translation parity and diff checks remain part of local verification.

Tests use Home Assistant 2026.2.3. They do not establish compatibility with every newer Home Assistant release, speech provider or hardware combination. After updating, check one normal voice command, a discussion ending, one exposed light with undo, and the gate announcement path on the installation.

## Useful next improvements

- A repeatable live voice check that records STT text, reply, selected pipeline and final listening state together, to separate recognition, model and microphone problems.
- Per-speaker delivery receipts, so partial delivery is clearly distinguished from a message reaching every intended speaker.
- Test coverage against a second Home Assistant version before changing the supported/tested version.
