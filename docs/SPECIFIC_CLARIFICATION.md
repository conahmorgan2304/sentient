# Specific device and value questions — 0.93.0

Enable **Settings → Voice → Specific device and value questions** alongside **Conversation sessions**, using the **Sentient with backup** agent. The new option is off by default. Enable **Connected follow-ups and corrections** too if you want an ambiguous adjustment of previously discussed devices to produce a local choice list without another model call.

## What changes

When the conversation agent needs a device choice, it can call `sentient_clarify` with two to five real candidate entity IDs. Sentient filters access, uses current device/room names and returns a numbered question such as:

> Which device: 1, Ceiling light (Kitchen); 2, Counter lights (Kitchen)?

The final reply uses this question, so the numbers correspond to the stored choices. Replies such as “the second one”, “2”, “Counter lights”, a uniquely matching room, “both” (exactly two choices), or “all of them” resolve locally. “No, I meant the counter lights” can select a different offered device while retaining the original request. Ambiguous rooms, qualified replies and duplicate labels do not silently select a device. A device name that conflicts with an ordinal is rejected rather than guessed.

For missing temperatures, the agent can request a temperature question. A reply such as “20 degrees”, “20.5 C” or “68 Fahrenheit” is captured as a value with its explicit unit, retaining any previously selected, still-accessible thermostat. This parsing does not apply the value, convert units or bypass device limits. The normal agent and guarded control must resolve the unit and validate the device's supported range.

Selections are supplied as structured context with the original request to the usual conversation agent. This avoids a separate AI classification call; the normal agent call to decide and perform an action is still needed. Local retries and existing-context device-choice questions can avoid model calls entirely. Speech translation, if enabled, retains its own normal caching/budget behaviour.

## Avoiding repeated questions

A reply that cannot be matched gets one clearer retry with the choices. A second unmatched reply closes that exchange and asks for a fresh full request, rather than repeating indefinitely. “Yes” does not choose a device. Explicit endings still stop the conversation. A new explicit command or information question discards the outstanding choice.

The tool rejects a request to ask for the same kind of detail again in the turn where it was already resolved. It cannot prevent every free-text question an external model might produce without using the clarification tool.

## Permission and lifecycle

- Choices belong to the existing conversation/device/user session and expire with that session. They are not resumed as pending permissions from remembered discussions.
- Candidate names, exposure and read permissions are checked before presentation and again before consumption. If choices become inaccessible or names/rooms change, the user must ask again; remaining choices are never silently renumbered.
- A choice or temperature is **not** action approval. Creating/resolving a question clears any old pending confirmation. Sensitive actions need a new proposal and separate confirmation through the existing guarded path.
- Sentient acting tools cannot start while a clarification remains pending. Guarded device dispatch also rechecks after permission awaits. Native tools belonging to other integrations are outside Sentient’s enforcement: use **Sentient guarded** for enforced device-action restrictions.
- Corrections retain prior action history, so the normal guarded correction path can acknowledge an action that already happened and request confirmation before a replacement. Nothing is silently undone or replayed by the clarification feature.
- Settings changes, explicit conversation endings and privacy clearing discard pending choices through the existing session lifecycle. Disabling the option removes its tool and clears pending state. Changing a selection does not undo earlier physical actions.

## Language and limits

Exact device and room names can be in any language. Numbered replies use digits; English ordinals, “both/all”, correction prefixes and simple temperature wording are recognised locally. Speech-language support translates the questions and is instructed to retain names and numbering order. This is not exhaustive multilingual short-answer parsing.

Initial candidate discovery usually depends on the configured model using the tool. Existing multi-device adjustment context can generate choices locally. No household entity IDs are hard-coded. Automated tests cover selection, access changes, ownership, stale asynchronous results, guarded dispatch and listening behaviour; actual microphone recognition and pronunciation still require live testing.
