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
