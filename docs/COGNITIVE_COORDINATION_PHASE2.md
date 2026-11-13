# Sentient coordination and intelligence — phase 2 review

This PR collects the remaining audit work in **one review branch**. Safety-critical additions are default-off or read-only. Automated tests are not a substitute for live Home Assistant and camera checks.

## Added implementation

| Workstream | Added | Still required before activation |
| --- | --- | --- |
| Action coordination | Preview+priority rules; optional per-motion-light protection after recent guarded voice-off, and optional auto-off protection after guarded voice-on. Both default off and only hold lower-priority reversible actions | Independent manual/automation ownership, verified outcomes across every action path, and real-device replay; no authority over alarms, locks or climate |
| Security outcomes | Admin review for retained incidents (household/visitor/false detection/concerning/unresolved), private persisted labels and aggregated stage comparison. Labels never alter alerts or Frigate thresholds | Independently validated real incident truth and representative long-term quality metrics; household labels alone are subjective |
| Memory | Lexical reranking wired to Brain search; optional LAN-only Ollama embedding reranking of existing authorised results, off by default, 2-second limit | User must provide a preinstalled Ollama model on a trusted LAN host, then evaluate accuracy and privacy; no external embedding model is installed automatically |
| Learning effectiveness | Bounded before/after comparison and real cumulative heating kWh comparison against matched weekdays, configurable in Heating settings | Reliable Recorder history, a dedicated kWh meter, enough matched days and weather/occupancy-adjusted baseline before asserting genuine savings |
| AI routing | Actual opt-in routing for qualifying background text-only AI tasks, using two preconfigured AI Task entities, 20+ measured successful/error service samples and user-entered cost estimates; never switches vision, structured or explicit jobs | Measure outcome quality and provider billing rather than treating service success and entered prices as proof of correctness/cost |
| Documents | Optional local image-page OCR using an already installed Tesseract executable, scanned-page detection and index refresh on OCR mode changes. Off by default, no cloud calls or model downloads | Tesseract executable is usually absent in standard HA OS; users must provide it separately. Validate real scans and citations before trusting results |
| Nest/streaming | Pure lifecycle evaluator requiring a first video frame, not merely a connected player | Real Nest stream traces, token refresh and stream-provider fallback testing |
| Speaker recognition | Aggregate matched/ambiguous/no-voice report with minimum sample thresholds | Collect consenting multiple-speaker ESPHome test traces; no identity-based authorisation |
| Custom pages | Explicit admin-controlled enable/disable with saved layout retention and original default restored when off; admin-only drag/drop, move/hide/restore/reset for Sentient built-in cards, HA-backed shared storage | Test tablet/browser drag behaviour and accessibility; arbitrary HACS card embedding is intentionally not enabled without a separate safe permission model |

### Security invariants
- Do not authorise actions from a recommendation, voice match, replay stage, learned outcome or stale context. Opted-in motion rules may *hold their own turn-on request* after voice-off; they never grant authority to execute another action.
- Alarm panels, locks, doors and climate are excluded from simulated reversible-action arbitration.
- Historical labels and data cannot change Frigate thresholds or disable alerts.
- Personal data must be permission-filtered *before* retrieval reranking.
- No models or cloud services are downloaded automatically. Local OCR requires an existing Tesseract executable; semantic reranking sends permitted text to the explicitly configured LAN embedding host only when enabled.
- Shared layouts contain built-in card IDs only; external HA card configuration is not executed.
- Existing cards remain in their normal layout until an administrator explicitly enables the feature. Turning it off restores original lane order and visibility without erasing the saved layout.
- The separate 3D talking-head prototype and unwanted floorplan incident replay are excluded.

## Review and live validation gates
1. CI: Python pytest, HACS, Hassfest and JavaScript tests pass on final HEAD, including ordered websocket access checks and persistence across restart.
2. Frigate: label camera+contact events independently, test false detections and outages; count unknown outcomes as unknown.
3. Home Assistant: leave both new coordination settings disabled until tested. Check motion-on after voice-off, auto-off after voice-on, two-minute expiry, and no impact to forced/manual commands.
4. Voice: multiple enrolled household voices, ambiguous matches and guest mode on physical ESPHome speakers.
5. Nest: capture real stream frame/error/timing events and check fallback for expired and stalled streams.
6. Documents: benchmark PDFs and scans with and without a locally installed Tesseract binary. OCR is optional and may not be present on HA OS; page citations are not correctness proof.
7. Model/provider: compare real billing, latency, quality and 20+ measured calls per provider before enabling opt-in text-only switching. Verify failed/unknown cases stay on primary.
8. UI: test drag/drop, arrows, hide/restore/reset on desktop and wall tablet, confirm browser restart persistence, guest/read-only access and no layout regressions. Arbitrary HACS-card embedding remains excluded.
9. Run restart, privacy-forget, permission and p95 performance regression tests on the user's HA OS host.

**Still requires external prerequisites and live evidence:** real Nest stream troubleshooting, real multi-speaker test, local Tesseract/Ollama installation, real provider bills, representative incident labels, sufficient energy history and tablet testing. Full autonomy and arbitrary HACS-card embedding remain intentionally out of scope until their permission/safety model is demonstrated. This PR is for code review, not automatic deployment.

## PR #148 review follow-up: fixes before merging

### OCR indexing recovery
Changing local OCR mode now builds the replacement index in SQLite TEMP staging tables and publishes files, pages, FTS rows and the mode marker atomically. A failed read or database write returns `reindex_completed: false` and leaves the previous usable index unchanged. Successful completion removes obsolete pages and updates the mode marker together. Regression tests simulate an extraction failure, successful scan migration and SQL rollback. If disabling OCR fails to rebuild, the earlier index remains in storage; investigate the error and retry rather than assuming OCR-derived data has been erased.

### AI routing calibration
**No automatic probing** is performed. Settings → Doorbell & AI looks has one-at-a-time administrator calibration controls. Each click requires a cost confirmation, counts against the normal AI call budget and is rate limited to one per 30 seconds. It sends a fixed generic prompt to the selected `ai_task` entity, with no voice, memories, images or live household state. Primary provider calls are also observed during normal operation. The UI displays completed samples and service metrics; 20+ samples per entity are required. These are service-call checks, not independent answer-quality measurements or real billing comparisons. Real token prices must be reviewed separately.

### Local semantic memory privacy
Only explicit numeric loopback, RFC1918 or IPv6 ULA URLs with HTTP and an explicit port are accepted; arbitrary hostnames, public ranges, link-local addresses and redirects are refused. The local model receives the query plus at most eight **already permission-filtered** snippets when the feature is explicitly enabled. This is still a data transfer to that LAN host: configure only a service you control, and do not enable it for a shared/untrusted server. Failed responses fall back to lexical search.

### Layout regression coverage
Node tests now exercise drag/drop lane ordering, server save payload, non-admin rejection and disabled-mode rejection. Python storage/WebSocket tests cover admin permissions and persistence. Real mobile browsers, touch interactions and accessibility still need manual smoke testing; no test claims to be a full browser simulation.

### Nest
The streaming evaluator remains diagnostics only. Record a failed Nest session with initial frame, errors and stream expiry, compare against a working Wyze stream, and validate changes on Home Assistant. Do not claim to have resolved an external Nest stream failure without that evidence.
