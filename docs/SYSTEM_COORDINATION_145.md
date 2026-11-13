# Sentient system coordination — draft PR #145

This document describes the first increment of a broad system audit follow-up. It is
**not** a claim of complete autonomous intelligence. This PR deliberately avoids
new service-call authority, model subscriptions, and alert suppression rules.

## Implemented

### Cross-feature dependency-chain diagnostics (follow-up)
- Config-entry diagnostics now include a fixed-category dependency-chain summary showing degraded versus unverified voice, camera, and heating feature prerequisites. It does **not** include camera names, room identifiers or user content. Availability is not a live end-to-end functionality check, and unmeasured voice/TTS/speaker services remain explicitly unknown.
- Regression tests cover unavailable, unknown and available prerequisite states.


### Privacy and memory consistency
- An awaited forget now removes stale copied facts from SQLite and its full-text
  index, **before** clearing its durable deletion intent. Previously fact
  copies could remain until the next memory-sync tick. Pinned or otherwise
  surviving live facts are preserved. Interrupted deletions continue to be
  replayed on restart.
- Backups are **not** erased by forgetting and may reintroduce old memories
  when restored. Diagnostics include counts and limitations without the
  backup's filenames or contents. Sentient's own JSON backup files are not
  independently encrypted; Home Assistant backup/storage access policies
  remain important.

### Heating validation (follow-up)
- After each normal heating history refresh, learned warm-up rates are checked against later held-out thermostat episodes. The zone report exposes the absolute warm-up-rate error only when there are at least two earlier training episodes and one later test episode. Insufficient history reports unknown rather than fabricated accuracy. This is rate validation, **not** proof of reduced bills, better comfort, or improved preheat timing. Thermostat actions, schedules and opt-in settings remain unchanged.

### Decision and action coordination
- A single bounded `ActionIntentMonitor` observes guarded voice,
  motion-light, auto-off and direct Rule dispatch requests. Opposite requests for the same
  target within two minutes from different subsystems are logged as
  **potential** conflicts. Same-source toggles and expired intents do
  not count. Targets are keyed-digested per runtime; summaries contain no
  entity names, commands from people, or room labels.
- The monitor neither blocks nor changes automation actions. It detects
  intent changes, *not* whether a device actually changed state. Private
  history is erased on a privacy-forget operation and withheld in guest mode.

### Shared speech duplicate identity (follow-up)
- Repeated equivalent wording differing only in punctuation is now suppressed across speech sources and addressed speakers within the existing duplicate window. Urgent/critical exemptions remain unchanged. Legacy exact-text delivery receipts are retained for compatibility.
- This is shared **text** identity, not event-level semantic deduplication; two differently phrased descriptions of the same incident may still be announced separately.

### Conversational working memory
- Bounded owner-local session working context retains the relevant goal/topic
  across a related follow-up, expires older context, and clears on subject
  change or session closure. No pending authorisation is inherited.
- Session descriptions say that historical evidence is stale and permissions
  must be rechecked. Unverified candidate entity IDs are never exposed
  from the additional context to the model; normal existing visibility
  filtering remains responsible for device context.

### Speaker-aware facts
- New memories support an optional explicit `owner` attribute, populated
  when a consenting, matched voice makes a first-person statement. Reading
  prefers this metadata to historical `Name: ...` prefixes. Existing memory
  records remain readable, with the legacy prefix fallback.
- This is optional personalisation, **not** identity verification and not
  authority to bypass a Home Assistant user or device permission.
- Existing typed, Echo and unidentifiable input limitations remain; users
  should avoid treating this memory scope as a secrets vault.

### Evaluation and replay
- To inspect synthetic examples without touching real Home Assistant devices, run
  `python scripts/cognitive_replay.py tests/fixtures/cognitive_scenarios.json --at 2026-11-13T12:15:00+00:00`
  from an environment with Sentient's test dependencies installed. This
  requires no camera, network, account or active Home Assistant connection;
  results contain aggregate counts only. Supply only synthetic or explicitly
  redacted fixtures. It is not the rejected floorplan replay UI.
- A pure offline `cognitive_replay.evaluate_scenarios` compares legacy and
  proposed incident stages with **independently supplied** labels. It uses one
  synthetic clock, reports unknown accuracy when no labels are present,
  never calls services and does not retain original incident text.
- `effectiveness.report` provides measured sample counts and rates from
  current diagnostics. It never infers improvement without a baseline.
- Mind exposes small, unrecorded aggregate summaries; guest mode hides
  these additions.

## Cross-system review gates for Claude

1. **Privacy:** delete recently created and pinned facts with the brain
   enabled; verify immediate SQLite/FTS deletion and restart replay after
   injected storage failure. Test concurrent sync with forgetting and backups.
2. **Permissions:** denied/unexposed/private entities must not enter working
   context or LLM prompts; check guest and cross-user sessions.
3. **Action overlaps:** run opposing requests from motion lights, auto-off and
   voice, with user overrides; monitor should report potential conflicts but
   never dispatch, block, undo or retry.
4. **Security replay:** evaluate false-positive gate detection, simultaneous
   garden/shed sightings, Frigate unavailable, and confirmed entry events.
   Distinguish correlation from identity. Do not use agreement as accuracy.
5. **Voice:** test multiple consenting voices and name sharing, typed and
   unmatched requests, first-person memory edits, backups and restores.
6. **Performance:** run Frigate bursts and common voice queries on HA OS;
   compare mean/p95 latency, CPU load, recorder sizes and event-loop stalls.
7. **Compatibility:** test Home Assistant restart, import/export, guest mode
   and settings changes; verify all existing HA Actions and CI workflows.

## Deliberately not enabled by this PR

- No autonomous promotion of Cognitive Core 2.0 from shadow mode.
- No motion-light/auto-off arbitration that changes real actions.
- No AI-based face identification, voice authentication or camera route proof.
- No network-dependent semantic embedding model or OCR service introduced
  on an Intel NUC without performance and privacy benchmarking.
- No automatic reconfiguration of heating or security alerts from learned
  routines. Such actions require independent opt-in, simulation results and
  confirmation/permission policy.
- No 3D talking-head project or floorplan incident replay.

## Suggested follow-up increments

Benchmark hybrid semantic+keyword memory retrieval and scanned-PDF OCR
against real household documents, add independently labelled security
outcomes, unify the *actual* action policy after shadow conflict metrics
are validated, and measure heating savings against a real baseline. These
are not claimed complete by the present PR.
