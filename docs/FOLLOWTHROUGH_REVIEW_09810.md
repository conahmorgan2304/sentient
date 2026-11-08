# Voice outcome checks — 0.98.10 review

The existing follow-through observer checks whether recently requested device states were reported. This pass fixes stale notifications and misleading certainty; it adds no optional feature, AI call or device action.

- Unload cancels retained timers and pending report tasks and clears the remembered voice source. Forgetting recent data and settings changes clear checks immediately too.
- Each check is tied to its current speech/privacy/settings context. An old queued report cannot publish after that context is invalidated or a newer request takes over.
- Device state is fetched again immediately before a queued report is produced, suppressing an old warning if the device has recovered. Duplicate timer checks do not schedule duplicate reports, and completion cleanup cannot remove a newer pending request.
- Non-finite/invalid temperature requests are not registered, and non-finite temperature feedback is treated as unknown.
- Opening/closing is not a completed cover/valve state. Where position is reported, a partially open cover is not treated as fully opened by an open command.
- Spoken feedback now says the requested state could not be confirmed. An offline entity is not proof that the physical appliance is off or failed. Timeline records use an unconfirmed-request category in their wording; the weekly report counts those separately from historical proven-failure entries.

The observer still uses the existing recent-voice proximity heuristic; it does not prove which person issued a command, and it does not verify every possible device attribute. A matching reported state is not independent physical verification. Already-dispatched speech cannot be recalled by invalidating a later check.

Validation: the full suite passes **1,400 Python tests** and **6 JavaScript tests**. This file's changes add **22 regression cases** in `test_followthrough_lifecycle.py`. Python compilation, frontend syntax, whitespace and English translation parity were checked. Real speakers and devices remain to be exercised after installation.
