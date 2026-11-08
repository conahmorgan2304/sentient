# Temporary heating ownership review — 0.98.7

This release improves existing Preheat, Heating time limit and Trust behaviour. It introduces no new optional feature, AI call or provider. Existing Settings controls remain in charge.

## Intention, submission and observation

Preheat used to create a restore record before its trust question was answered. It now creates a pending record only immediately before dispatching a current request. Declined, expired, changed or unavailable requests have no restore record. Pending submissions are reported separately from observed active temporary targets; a service response alone is not proof of heating or physical operation.

The thermostat must report the requested target/mode within ten minutes. Only then is its control state (HVAC mode, target and preset) recorded as owned for restoration. Room-temperature readings can change normally without invalidating ownership. Missing or unknown readings are not treated as successful commands. An unconfirmed request expires with a health diagnostic asking the user to check the thermostat; it is not retried or blindly restored.

Preheat rechecks the selected device, enabled setting, original controls, current need for heat, supported target range, never-touch list and lifecycle immediately before dispatch, including after a saved trust answer. A normal room-temperature update no longer invalidates an otherwise valid heating answer. A reply after the warm-by deadline cannot start a late preheat.

## Restore only what still belongs to this timer

State changes to the target, preset or HVAC mode revoke the restore record. A later external control request also cancels previous ownership, including a parent-context automation taking over. A repeated direct manual raise can renew its time limit while preserving the original baseline only when the previous boost is still observed and owned. An old turn-on timer cannot later switch off a thermostat whose controls someone changed.

Restore checks the exact record identity, current feature selection/enabled state, never-touch rules, lifecycle and owned controls. It consumes that exact record before awaiting the service, preventing duplicate submissions and protecting a newer timer from an old queued task. Previous target values outside current thermostat limits are not restored.

An observed boost on a temporarily unavailable thermostat can wait up to one hour after its due time for matching controls to return. Later or changed states are left alone. These are conservative state comparisons, not proof of who changed a physical thermostat, and cannot detect an unobserved change away and back while Home Assistant was offline.

Successful restoration is logged as a request, with the intended previous preset/target/off state. A failed request is explicitly unconfirmed in the health timeline and is not automatically retried, because it may already have reached the device. Already-dispatched actions cannot be recalled by a settings change. Late results after forgetting/settings/unload do not recreate old completion notes.

**Upgrade treatment:** older stored heating timers lack the new ownership evidence and are discarded rather than blindly executed. Check any temporary heating active during the upgrade and restore it manually if needed. Disabling the relevant feature or excluding the device cancels its automatic restore (an empty Preheat selection retains the existing all-thermostat discovery behaviour); it does not reverse an earlier command.

## Shared trust and learning

Exceptions from a background Trust callback no longer become a successful “Request sent” or escape as an unhandled saved-answer failure. Do/tell callers receive an unconfirmed outcome, and a saved yes gets a plain uncertainty reply. The trust success streak is not increased. Returning False continues to mean the caller refused before dispatch; the legacy None-return contract is retained for existing callers.

Heating history learning is serialised and rechecks settings/privacy/lifecycle across awaits before publishing. Recovered unknown states are not counted as human switch-on evidence. Unknown/unavailable comfort states and negative durations are excluded; non-finite/non-positive warm-up rates cannot crash planning or trigger preheat.

Live testing should cover a declined preheat, a delayed thermostat update, changing target/preset after a boost, a failed restore, and an active temporary target across reload. Device-specific preset/schedule behaviour still needs validation on real hardware.

## Validation

All **1,273 Python tests** pass, including **44 new regression cases** in `test_heating_ownership.py`. All **6 JavaScript tests** pass. Python compilation, frontend syntax, patch whitespace and identical English translation files were checked. The focused heating/shared-trust regression set also passes after the final diagnostic assertion. Live thermostat/preset behaviour, delayed real updates and device outages remain to be tested after installation.
