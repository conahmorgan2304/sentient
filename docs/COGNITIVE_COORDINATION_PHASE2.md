# Sentient coordination and intelligence — phase 2 review

This PR collects the next audit work in **one draft**. These foundations are deliberately read-only and must not be described as finished automatic control or full cognitive autonomy.

## Added implementation

| Workstream | Added | Still required before activation |
| --- | --- | --- |
| Action coordination | Pure priority/expiry policy and ActionIntentMonitor.preview; optional per-motion-rule protection from motion-on immediately reversing a recent guarded voice-off request for light/switch targets, off by default; alarms, locks and heating unchanged | Full cross-feature arbitration beyond motion lights, independent confirmation of device outcomes and replay of real competing automations |
| Security outcomes | Independently labelled scenario aggregate scorer with sample limits; unresolved outcomes excluded | Consent-gated UI for verified labels, ground-truth review and longitudinal reliability measurement. Existing Frigate feedback is not independent verification |
| Memory | Read-only lexical candidate reranking and labelled recall@5 benchmark | Benchmark true semantic embeddings locally against private examples; never expand permission scope |
| Learning effectiveness | Bounded comparison of observed before/after completion metrics | Establish trustworthy baselines and account for changes in sensors/occupancy; no causal improvement claims |
| AI routing | Advisory model selection from supplied measured success, cost and p95 latency samples | Collect comparable real task observations and opt in to actual routing; current models remain unchanged |
| Documents | Scanned-page extraction coverage and lexical citation-support diagnostics, accessible via pages.quality_report | Test OCR engines locally and build evidence-linked citations; nothing sends scanned documents to cloud OCR |
| Nest/streaming | Pure lifecycle evaluator requiring a first video frame, not merely a connected player | Real Nest stream traces, token refresh and stream-provider fallback testing |
| Speaker recognition | Aggregate matched/ambiguous/no-voice report with minimum sample thresholds | Collect consenting multiple-speaker ESPHome test traces; no identity-based authorisation |
| Custom pages | Admin-only layout save/reset backend and optional in-panel edit controls for moving cards between lanes, hiding/restoring and reset. Saved in HA storage and read on other devices | Live tablet testing, accessibility review, hide-to-stop-fetch and unrestricted Home Assistant/HACS card rendering (not yet implemented) |

### Security invariants
- Do not authorise actions from a recommendation, voice match, replay stage, learned outcome or stale context. Opted-in motion rules may *hold their own turn-on request* after voice-off; they never grant authority to execute another action.
- Alarm panels, locks, doors and climate are excluded from simulated reversible-action arbitration.
- Historical labels and data cannot change Frigate thresholds or disable alerts.
- Personal data must be permission-filtered *before* retrieval reranking.
- No cloud services, model downloads or external dependencies were added.
- Shared layouts contain built-in card IDs only; external HA card configuration is not executed.
- Existing cards remain in their normal layout until a saved layout is applied.
- The separate 3D talking-head prototype and unwanted floorplan incident replay are excluded.

## Review and live validation gates
1. CI: Python pytest, HACS, Hassfest and JavaScript tests pass on final HEAD.
2. Frigate: label camera+contact events independently, test false detections and outages; count unknown outcomes as unknown.
3. Home Assistant: leave the new Protect voice overrides setting disabled until tested; then ensure no unexpected motion-on after voice-off during the 120-second override window and confirm normal operation after expiry.
4. Voice: multiple enrolled household voices, ambiguous matches and guest mode on physical ESPHome speakers.
5. Nest: capture real stream frame/error/timing events and check fallback for expired and stalled streams.
6. Documents: benchmark text PDFs and scans; OCR is still not installed, and citation support is not correctness proof.
7. Model/provider: collect real costs, latency and task accuracy before enabling any automatic choice.
8. UI: test moving/hiding/reset on desktop and wall tablet, confirm browser restart persistence, guest/read-only access and no layout regressions. Drag-and-drop and arbitrary HACS-card embedding remain unimplemented.
9. Run restart, privacy-forget, permission and p95 performance regression tests on the user's HA OS host.

**Not yet done:** live device test, full automatic arbitration, semantic embeddings, OCR implementation, actual cost-based model switching, drag-and-drop, custom HA-card embedding, and full dashboard editor validation. They must remain disabled / unfinished until explicitly validated. This PR is for code review, not automatic deployment.
