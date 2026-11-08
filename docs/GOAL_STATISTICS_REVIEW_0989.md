# Goal statistics review — 0.98.9

The existing optional Goals feature now uses comparable daily values rather than sample-count averages or a counter's highest-minus-lowest reading. No new feature toggle, AI call, device action or background polling loop is introduced.

## Recorder statistics first

Where available, use Home Assistant's daily recorder `change` statistic for total/total-increasing sensors and its daily `mean` for measurements. This reuses recorder's reset-adjusted sums and native mean semantics. Requests cover complete local calendar days and exclude the current partial day. Returned start/end boundaries, finite values and units are checked. Known unit-class conversions are requested explicitly; unconvertible mismatched units are not silently compared.

If native statistics cover every requested day, no second raw-history query is made. For other days, raw history is processed in the recorder executor and native results take precedence for overlapping dates. This keeps database work off the event loop. Native statistics retain Home Assistant's own coverage and aggregation semantics; a daily row is not proof that every physical observation was available.

## History fallback

- Scalar measurements are weighted by the time their recorded value lasted, rather than the number of updates. A sensor emitting many readings in one hour no longer outweighs the other 23 hours.
- Total-increasing counters add observed increases across resets. Signed totals use net changes rather than max-minus-min, and explicit `last_reset` changes are respected.
- A real state before the query window can anchor the first day without creating an out-of-window result. An unchanged recorded counter can have zero observed change; absence of usable history is not invented as zero.
- Unknown, unavailable, non-finite, incompatible-unit/type or missing initial intervals prevent that raw day from being treated as complete. A later valid reading cannot silently bridge an unknown interval.
- Boundaries use actual local midnights in UTC, so 23/25-hour clock-change days and half-hour offsets are handled correctly. The current/end-boundary day is excluded.

Fallback increments belong to the local date when they were reported. They are not interpolated across midnight, and unreported physical consumption is not extrapolated. State values are assumed to last until Home Assistant records a change. No calculation can recover an unreported reset/outage or determine how consumption was distributed between distant samples; native meter statistics are preferred.

## Comparable goal records

New goals store the statistics method version, source unit/state/device class and number of baseline days. Small nonzero baselines retain their precision instead of being rounded to zero before comparison. A zero baseline gets an explanation rather than a percentage division.

On their next enabled check, open legacy goals rebuild their baseline from the seven completed calendar days before their original creation, and rebuild eligible daily checks from retained history. Old derived percentages are not mixed with new-method readings. Goal title, intent and dates are preserved. If original comparable history is gone, the baseline becomes unavailable; it is not replaced with behaviour from after the goal started. Percentages can change or disappear during this refresh because the previous method was inaccurate or the history is no longer available. Closed legacy archives are not backfilled automatically.

Checks exclude pre-creation dates, the current partial day and days at/after the goal's ending calendar date. Calendar dates are the comparison unit; these are not exact continuous-time measurements beginning at the instant a goal was created. Overdue checks read the goal's final historical days instead of unrelated post-goal data.

Changing a sensor's unit or type pauses comparison and requires a new goal for a comparable baseline. The page/tool data does not relabel an old kWh baseline as Wh. Temporary unavailability pauses without automatically turning it into a permanent unit-change decision. Late results still honour existing settings/privacy/lifecycle and goal-identity checks.

Progress says “across N recorded days”, avoiding the claim that missing or irregular checks represent consecutive days. Baseline-day counts and explanatory notes are available through goal results/listing, and notes reach the existing conversation prompt. One recorder failure does not block checking other goals; an expired goal still closes with an honest unavailable-data note.

## Validation

All **1,377 Python tests** pass, including **46 new regression cases** in `test_goal_statistics.py`. All **6 JavaScript tests** pass. Python compilation, frontend syntax, whitespace and English translation parity were checked. The existing sensor-goal fixture now uses dates that belong to the goal period it asserts.

Recorder's public statistics/history interfaces were checked against the installed Home Assistant source. Test cases cover resets, signed totals, explicit resets, weighting, missing intervals, units, source priority, clock changes, half-hour zones, baseline migration, precision, expiry and cancellation. A live household and its historical recorder database were not available; compare several real meter/temperature days against Home Assistant after installation.
