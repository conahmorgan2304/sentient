# Virtual Sentient — test scope and evidence

This branch adds `scripts/replay/virtual_suite.py` as an extension to the existing
`scripts/replay.py` and `scripts/replay/runner.py` infrastructure.

## Running

```sh
python scripts/replay/virtual_suite.py --out /tmp/sentient-virtual --generate-only
python scripts/replay/virtual_suite.py --out /tmp/sentient-virtual
```

The second command requires the project's Home Assistant pytest dependencies.
Read `/tmp/sentient-virtual/report.json` for each replay's execution status.

## Safety

The existing replay runner registers fake device, speech and notification
services, records calls, and replaces the AI gateway with an unavailable
stub. It must run only against a test Home Assistant instance. Never point
the replay at production credentials or a live Home Assistant server.

## Scenario coverage

- Security: gate-to-shed motion sequence, duplicate gate changes, unknown contact.
- Automations: overlapping human and automation light changes.
- Device control: unavailable climate and unknown temperature observations.
- Resilience: integration reload during an active gate event.
- Performance: burst of 100 alternating motion changes.
- Context: stale/unknown temperature readings.
- Cameras: motion proxy only, **not** a real Frigate MQTT payload.
- Privacy: kitchen motion proxy only, **not** a guest-mode permission test.

## Important gaps — not tested by this scenario runner

- Conversation/listening policy and voice pipeline end-to-end.
- Memory recall, correction, expiration and person isolation.
- Actual Frigate MQTT, clips, streams, camera models and Nest integration.
- Voice recognition confidence, concurrent speaker matching and cross-turn identity.
- Real device permissions, actuation, confirmation, and state verification.
- Actual automation execution and action loops.
- AI reasoning quality, response schemas, latency, and provider fallbacks.
- Guest/private-room access enforcement and prompt injection.
- Hardware CPU/RAM consumption, latency percentiles, long-term DB growth.

These need separate Home Assistant integration tests and real-device validation.
A successful replay means the scenario executed without crashing; it does
**not** mean every behavioral decision was correct. Inspect decisions,
speech, phone notifications, AI request counts and recorded device actions.
Do not report unrun tests as passed.

## Follow-up

Add expected-decision assertions per fixture, a Frigate event injector,
scripted AI responses, conversation turn fixtures, and explicit privacy
and permission tests before describing this as full virtual Sentient.
