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

## Follow-on work in this PR and future iterations
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

### CI failure correction and outcome verification
- The previously failing 13 replay-test cases were expecting the microphone to remain open after an isolated light command in a live discussion. The expected assertions now match the chosen one-shot listening policy: discussion questions keep the microphone open, while the light command closes it. Explicit ending phrases are still checked.
- Guarded multi-step execution now reports a non-actuating `assessment` of its actual outcomes. A `verified` device state is a Home Assistant report, not physical proof. Partial, failed and unverified batches never authorise automatic replay; check the device before making a fresh request.

### Outstanding substantial work
- More useful cross-room, speaker-scoped preferences require careful consent, matching and privacy tests; the existing per-turn integration remains in place.
- A richer offline LLM and semantic PDF search would be separate optional dependencies and should be benchmarked on the deployed hardware before offering as defaults.
- Sensor-fusion alert decisions should initially run alongside existing security alerts without changing delivery, false-positive handling or safety escalation until comparison tests are complete.

### Additional security reasoning and CI alignment
- Investigated actual GitHub pytest logs: 13 failures were all `test_discussion_replay_stays_open_until_user_finishes` variants expecting the old microphone behaviour after a one-shot device command. Updated these expectations to protect the established one-shot-close rule while keeping question-based discussion and verbal termination tests.
- Camera contact + person detection reports can now be distinguished from independently **observed** contact + camera activity. Neither establishes identity, intrusion, or a particular route; alert decisions remain unchanged.
- Partial guarded-control action results now include an explicit outcome assessment, advising verification before a new request rather than automatic retry.
- New tests cover outcome evaluation and sensor signal corroboration. Review latest full CI before release.

### Review fixes before merge (1.3.21)
- **Security wording:** an alarm going off while a door or lock had no settled reading (or with no perimeter sensors at all) skipped the "I can't confirm the whole perimeter" wording and could say "the monitored openings report closed". Any coverage gap now keeps the uncertain wording, as before this PR.
- **Stopping a cover:** plan review marked every cover action for confirmation, so "stop the garage door" waited for a yes. Only opening and closing covers, and locks, ask first; stopping is immediate again.
- **Incident evidence:** Frigate events are recorded with kind `event`, which the assessment ignored, so a gate contact plus a Frigate person event was never counted as camera corroboration. `event` now counts as camera evidence, and doorbell/doorstep visits are named. Neither proves identity or route.
- **Prompt size:** the world assessment repeated every reading (already in the snapshot with its age) on every voice turn. It now carries only counts and the readings that need care.
- **Manuals offline:** the text search only chose a file and still needed the AI. When the AI can't be reached it now quotes the closest passage from a `.txt`/`.md` document (bounded, no symlinks, no parent paths). Both the inside-text search and the quote sit behind a new switch, *Look inside text documents* (on by default; off matches file names only).
- **Mind sensor:** incident assessments are worked out only for the eight incidents returned, not every stored one.
- **Outcome wording:** a batch where a step failed and nothing was verified is now reported as `failed`, not `unverified`.


## Cognitive Core 2.0 — shadow comparison (1.5.5)

`cognitive_coordinator.py` is a **read-only second opinion**, off unless Settings → Safety & security → "Compare security
incidents (testing)" is on. Switched off, nothing is compared and the counts start again.

- **Incidents:** `Incidents.recent` keeps the existing `stage()` as the one that counts and adds
  `cognitive_shadow`: a stricter stage (observed, investigating, likely household, concerning, resolved or
  unknown) from the same evidence, with what it couldn't be sure of. Agreement is counted once per change in an
  incident's evidence, so redrawing the Mind card doesn't inflate it, and is reported only after ten incidents.
  Contact and camera evidence back each other up only when they come from different sources; neither proves who
  someone was or where they went.
- **Actions by voice:** `Control` adds a step-by-step summary of how a guarded batch went (verified, attempted,
  failed, not started). It never retries a step or grants anything.
- **What it never touches:** alerts, stages, speech, permissions, device control and what the voice is told. The
  house context sent to the voice has nothing from it.
- **Privacy and size:** the Mind sensor shows totals only (`cognitive_shadow`), hidden in guest mode and not
  kept by the recorder. Incident ids are kept as keyed digests for this run only, and at most 128 of them.
- **Before anything acts on it:** compare enough distinct real incidents, look at every disagreement, test
  unavailable sensors, restarts and overlapping episodes, and measure the cost on the house's own hardware.
  A disagreement is something to look at, not proof that either way is right.
