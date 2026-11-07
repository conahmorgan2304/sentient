# Language reliability and conversation recovery — 0.90.0

## Speech language

The existing **Settings → Voice → Say built-in sentences in the house's language** switch remains off by default. English houses and disabled translation bypass translation as before.

Ordinary English-looking sentences use the existing AI gateway with its ten-second limit, budget and English fallback. The detector is a heuristic, not a guarantee of language identification. Fixed Finnish and French translations cover built-in smoke, carbon-monoxide, gas, leak, safety, tamper and alarm announcements, preserving room/device names. Urgent messages never wait for a live AI translation: they use a fixed phrase, an existing cached translation or immediate original text. Unsupported languages therefore retain immediate English fallback for urgent messages.

The bounded 300-entry cache belongs to the current integration runtime. Settings changes, forgetting recent data and unload clear it. A translation finishing after a clear cannot repopulate it. It is not saved across restarts.

Question-to-announcement fallback reuses the one translation attempt, including failures. Spoken text is returned and recorded consistently; duplicate detection and follow-you delivery retain the original text. Partial/cancelled deliveries also retain original duplicate keys so accepted destinations need not be repeated.

With speech-language support enabled, explicit Finnish/French yes/no replies and conversation endings are recognised before AI handling. Localised generic follow-up questions work with a plain local-language no. Qualified positive replies do not approve actions. Alarm/guarded confirmations still require their original separate-turn and ownership checks. This is limited locale support, not complete multilingual intent parsing; the connected-follow-up phrase recogniser remains English-based, and some deterministic status replies remain English.

## Cancellation and failure recovery

Enable **Settings → Voice → Conversation cancellation and cautious recovery**, alongside **Conversation sessions**, with the **Sentient with backup** agent. The new option is off by default.

- “Actually, leave it” or “cancel that” cancels a pending request. If a request is still running, an identified user/device in the same conversation can cancel waiting for it. Another device or user cannot cancel that task.
- A second request from that same conversation is not queued behind the running action; Sentient explains how to cancel it first.
- Cancellation does not promise rollback. An action may already have reached Home Assistant or the device. Nothing is automatically undone or replayed.
- After an AI failure, recognised device commands, relative adjustments and corrections are not sent to backup when this option is enabled. Information requests can still fall back.
- Independently of that switch, calls to Sentient acting tools mark the request as potentially acted upon, preventing backup replay after failure. Sentient acting tools also reject late calls from closed sessions.
- With **Connected follow-ups and corrections** enabled, “No, the other light” asks which device/room instead of selecting one silently.

This cannot make a microphone accept speech while its hardware/pipeline is busy. Cancellation is available when a new input actually reaches the agent, and only cancels the local request task. Provider work or device operations already dispatched may continue. Native tools from other integrations are outside Sentient's tool guard; use guarded control for enforced device action restrictions. The cautious native-command check is a phrase heuristic, not a complete intent classifier.

Disabling the recovery option stops its extra cancellation/busy/command-fallback handling. Existing Settings changes already close sessions and invalidate pending authorisations; disabling does not undo an action or terminate a request already sent to a provider. The existing one-shot command, question-based listening and explicit conversation-ending policies remain in force.

## Validation

Regression tests cover fixed alerts without AI calls, language-cache forgetting/unload, deletion during translation, one-attempt question fallback, translated question records, original duplicate keys on cancelled speech, local exact confirmations/endings, same-owner cancellation, different-device isolation, disabled settings, ambiguous corrections and suppression of backup replay. All use controlled Home Assistant/provider doubles; live device audibility and hardware interruption still need installation testing.
