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

### Local document retrieval
The existing manuals answer service now falls back to a bounded local keyword search when filenames do not match. The search inspects at most 40 sorted `.txt`/`.md` files and 128 KiB per file, rejects symlinks, never traverses parent paths, and refuses tied matches. PDF and image processing remain unchanged. No background indexing or extra AI calls.

### Offline resilience
The existing voice-backup mechanism already routes eligible requests to a configured local backup conversation agent when the main provider is unavailable. This PR preserves it and adds deterministic, non-network assessments that can be reused locally; it does not pretend to supply full offline open-ended conversation or install an LLM.

### Rollout/testing checklist
- Confirm CI for the latest branch HEAD, not only older intermediate commits.
- Run manual HA tests for a normal voice command (closed), an information question (follow-up), and an explicit `no thank you` ending.
- Check security with closed contacts, unavailable contacts, active alarms and permission-limited users.
- Check camera timeline compatibility and alert deduplication.
- Check household guest mode and private-room context do not expose extra entities.
- Verify that text-file document selection stays within the configured media folder.

### Guarded planning and verification
`plan_review.review_plan` now runs after the existing `Control.validate` permission, exposure and never-touch checks. It rejects conflicting target steps, bounds the batch to 20 actions, and independently marks lock and cover operations for separate-turn confirmation. `Control` still owns execution, per-target verification and recovery; the review alone is never permission to act. This is useful multi-device planning but **not** arbitrary autonomous task execution.

### Local security reasoning
`local_mind.explain_security_offline` produces a deterministic, no-AI explanation directly from authorised security reports. A missing or partially unavailable sensor stays uncertain. This is included as an `offline_explanation` on the existing security report, for callers to use without depending on a cloud model. It does not replace the existing voice fallback agent.

### Speaker identity scope
Sentient already has per-turn voice recognition with opt-in name sharing and cautious unknown-speaker handling in `voice_id.py`. The existing person attribution and guest/privacy safeguards remain active; no cross-person session sharing or speaker-based unlocking has been introduced. A future speaker-based preference layer must require explicit consent and real-home testing; a voice similarity score is not proof of identity.

### Follow-up reliability and privacy hardening
- A one-shot device command made during an active information discussion no longer inherits the generic "anything else" prompt. A true question can still open the expected follow-up, preserving explicit voice-end handling.
- Guarded `sentient_incidents` now computes its assessment **after** `readable_evidence` permission filtering, rather than using the unfiltered episode summary.
- Future-dated sensor reports (significant clock skew) are marked undated instead of current, with regression coverage.
- Latest branch validation must pass again after these amendments.
