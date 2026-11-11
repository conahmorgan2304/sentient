# Cognitive foundation — phase 1

This change starts the shared observe → assess → explain architecture without altering control permissions, event subscriptions, or speech behaviour.

## Contract
- Inputs are already-visible facts from the existing security checker.
- The pure, deterministic assessor returns `problem`, `unknown`, or `clear` with machine-readable reason codes.
- A definite problem takes precedence over missing readings, but the `confirmed` flag stays false if coverage is incomplete.
- Unknown, restored and access-limited state must never become a claim of safety.
- The assessor does not call Home Assistant services, AI, speech, or notifications.
- Existing security response fields and user-facing wording remain unchanged.

## First integration
The current `security.check` and voice-filtered `check_for_voice` paths use the same assessor. Existing security and incident tests remain the compatibility baseline; `tests/test_cognitive.py` covers the new boundary.

## Follow-on work (separate PRs)
1. Add bounded, timestamped observations and uncertainty/provenance metadata to a shared world snapshot without duplicating the HA state machine.
2. Pilot this assessor with camera incidents and night watch in shadow mode; compare outputs before allowing it to affect alerts.
3. Add a conversation-state evaluator that respects existing one-shot commands and explicit stop phrases.
4. Add decision/outcome coverage to CI, including permissions, safety bypasses and graceful offline fallback.

This PR intentionally does **not** introduce autonomous actuation, an always-listening loop, or persistent personal profiling.

## Further implementation on PR #127

### Permission-filtered world model
`world_model.summarise_world` operates on `HouseContext.snapshot` only **after** Assist exposure, Home Assistant user permissions, private-room and guest-mode filtering. It produces bounded provenance, age and uncertainty annotations, and never scans devices independently. Older reports are not automatically called sensor failures. It does not grant action authority.

### Conversation listening policy
`conversation_policy.decide_listening` is used by `Sessions.finish`. It explicitly preserves the existing speech rule: a final response ending in a real question may keep the microphone open; a one-shot non-question does not. Explicit spoken endings are still handled by the original termination logic. It does not bypass Home Assistant's pipeline limitations.

### Multi-source security investigation
`incident_reasoning.assess_incident` augments read-only `Incidents.recent()` responses with observed/inferred/unknown evidence, source counts and reasons. Time correlation is *never* used to establish identity or a confirmed route. This layer does not change alert escalation, deduplication, Frigate requests, or speech policies.

### Scope and limitations
The existing HA voice backup continues to provide eligible local fallback; this PR does not claim to offer an unrestricted offline LLM. Document retrieval remains an independent feature requiring future measured indexing improvements. Runtime behaviour still needs CI and Home Assistant hardware testing before merging.
