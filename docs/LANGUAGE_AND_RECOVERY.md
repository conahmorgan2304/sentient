# Language reliability and conversation recovery — 0.91.0

## Automatic language setup

In **Settings → Voice**:

1. Enable **Say built-in sentences in the house's language**.
2. Enable **Automatic language support** (off by default).
3. Leave **Speech and conversation language** at `auto` to use Home Assistant’s language, or choose a code such as `es`, `de`, `pt-BR`, `ar` or `ja`. Custom language codes are accepted; the dropdown is not a support whitelist. Invalid codes fall back to English.
4. Use matching speech recognition and a TTS voice in the Assist pipeline. Sentient cannot add languages to those engines or reconfigure the pipeline. For Sentient’s own selectable TTS engine it requests the chosen language if supported; otherwise the engine’s existing fallback applies.
5. Enable **Conversation sessions** with the **Sentient with backup** agent for deterministic conversation endings. Add **Conversation cancellation and cautious recovery** if you also want cancellation of running requests.

Normal AI conversations and user-facing background prose receive the selected language instruction. Existing AI tool schemas, IDs and required classification labels must remain unchanged. English-only defaults do not run translation/preparation calls.

For non-English languages, the first ordinary utterance or session turn prepares a small phrase pack through the existing AI Task: a generic follow-up, acknowledgement, common explicit endings, negative replies, and safety labels. This may add up to ten seconds on first use. Concurrent preparations share one call; failures are retried on a later request after a five-minute cooldown, never polled. Both preparation and translation count towards the existing speech-language AI budget. A failed, malformed or over-budget preparation leaves the ordinary translation/original-text fallback in place.

The pack works with any language the configured AI can translate, without writing a separate integration for each language. Quality varies by provider/language; this is broad automatic support, not a claim that every language has been validated. Ordinary translation still uses an English-word heuristic, so some short or mixed-language sentences may be missed or unnecessarily translated.

Prepared endings are exact matches, never substrings. A bare negative closes a generic “anything else?” exchange, but not a specific question. Qualified replies remain with the conversation agent. Unknown wording is passed to the normal AI; a phrase pack cannot cover every dialect or expression. Arabic and full-width question marks count as questions for listening policy.

**Permission remains separate.** Generated phrases can only end/reject, never grant consent. Fixed exact yes/no tokens cover Finnish, French, Spanish, German, Italian, Portuguese, Dutch, Polish, Swedish, Danish, Norwegian, Turkish, Russian, Ukrainian, Arabic, Hindi, Bengali, Japanese, Korean, Indonesian, Vietnamese, Hebrew, Greek, Czech, Romanian and Hungarian. Qualified positive answers are not accepted. English confirmation remains available; other languages without a fixed token must use an existing accepted confirmation, and all separate-turn/ownership/expiry checks still apply. This is not a replacement for multilingual STT or full multilingual parsing of Sentient’s rule-based corrections.

## Speech reliability and privacy

Ordinary English-looking sentences use the AI gateway with a ten-second limit and original-text fallback. Repeated translations are cached in a bounded 300-entry memory cache. Common prepared follow-ups can be attached to already-localised replies without another translation call.

**Urgent speech never prepares a pack or waits for live translation.** Fixed Finnish/French safety wording takes precedence. Other languages use prepared safety labels when available, an existing cached translation, or immediate original English. Household names are appended locally to labels and preserved. After a restart or cache clear, an urgent alert may therefore be English until the next ordinary use prepares the pack. Machine-translated safety labels require real-house language checking; never depend on Sentient as the sole alarm.

The runtime owns the pack and translation cache. Settings changes, forgetting recent data and unload clear both, including in-flight results; nothing is stored across restarts. Disabling Automatic language support clears prepared data and stops its preparation/conversation handling; the separate built-in speech translation option can remain on. Disable that too to stop all optional language work. Already dispatched AI requests cannot be recalled.

Question-to-announcement fallback reuses a single translation attempt. Duplicate detection and follow-you delivery retain the original text. Actual spoken translations are returned by the speech engine, and automatic conversation replies update the waiting question text. Existing session history can still include the original deterministic wording recorded before the final translation.

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
