"""Bounded security episodes. Time correlation is explicitly not identity tracking."""
from __future__ import annotations

from datetime import timedelta
import uuid

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .sessions import CONF_INCIDENTS
from .cognitive_coordinator import (
    CONF_COGNITIVE_DECIDES,
    CONF_COGNITIVE_HEADS_UP,
    CONF_COGNITIVE_SHADOW,
    CognitiveCoordinator,
    authority,
    decided_stage,
    investigate,
)

MAX_REVIEWS = 300

CONF_COVERAGE = "security_coverage"
CONF_STAGES = "incident_stages"
RESOLVED_AFTER = timedelta(minutes=30)


def stage(item, assessment=None, *, now=None):
    """Where an incident has got to: observed, investigating, likely household, concerning or resolved.

    Built only from what was recorded (alert levels, sources, what was kept quiet and why); it never claims who
    someone was or where they went."""
    texts = ' '.join(str(e.get('text', '')) for e in item.get('evidence', [])).lower()
    sources = len(item.get('sources') or [])
    level = item.get('alert_level') or 0
    end = dt_util.parse_datetime(item.get('end') or '') or dt_util.utcnow()
    household = 'someone from the house' in texts or 'false alarm' in texts or 'household' in texts
    if (now or dt_util.utcnow()) - end > RESOLVED_AFTER:
        if household:
            return 'resolved', 'quiet for half an hour; most likely someone from the house'
        return 'resolved', 'nothing new for half an hour' + (' after an alert' if level else '')
    if household and level < 2:
        return 'likely household', 'an entry door opened just before, or it was marked as a household event'
    if level >= 2:
        return 'concerning', 'alerted while the alarm was set, everyone was out, or someone stayed a while'
    if level == 1 or sources > 1:
        return 'investigating', 'more than one camera or sensor involved' if sources > 1 else 'alerted at night'
    return 'observed', 'one report so far'
GAP = timedelta(minutes=3)
ALERT_LEVELS = {'night': 1, 'away': 2, 'fence': 2, 'loitering': 3}


def configured_cameras(hass, options):
    from .camsecurity import camera_for
    from .const import CONF_CAMSEC_SENSORS, CONF_CLIP_CAMERAS, CONF_DOOR_CAMERA

    cameras = set(options.get(CONF_CLIP_CAMERAS) or [])
    if options.get(CONF_DOOR_CAMERA):
        cameras.add(options[CONF_DOOR_CAMERA])
    for sensor in options.get(CONF_CAMSEC_SENSORS) or []:
        if camera := camera_for(hass, sensor):
            cameras.add(camera)
    return {f"camera.{camera.removeprefix('camera.')}" for camera in cameras if isinstance(camera, str)}


def public_evidence(row):
    return {k: v for k, v in row.items() if k not in ("camera_gaps", "read_source")}


class Incidents:
    def __init__(self, hass, entry_id, options):
        self.hass, self.options = hass, options
        self._store = Store(hass, 1, f'sentient.{entry_id}.incidents')
        self.items = []
        self.reviews = []  # the household's verdicts, kept after the incident itself is gone (no evidence, no names)
        self.on_authority = None  # called with a line for the timeline when the core starts or stops deciding
        self._earned = None
        self._pending_alerts = {}
        self.cognitive = CognitiveCoordinator()  # bounded, read-only shadow comparisons
        self._unsub = None

    @property
    def enabled(self):
        return bool(self.options.get(CONF_INCIDENTS))

    async def async_start(self):
        data = await self._store.async_load() or {}
        self.items = data.get('items', [])
        self.reviews = data.get('reviews', [])[-MAX_REVIEWS:]
        self._earned = authority(self.reviews)["earned"]
        self._prune()
        self.configure(self.options)
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed)

    @callback
    def async_stop(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _prune(self):
        cutoff = (dt_util.utcnow() - timedelta(days=7)).isoformat()
        self.items = sorted((i for i in self.items if i.get('end', '') >= cutoff),
                            key=lambda i: i['end'], reverse=True)[:100]
        live = {i['id'] for i in self.items}
        self._pending_alerts = {k: v for k, v in self._pending_alerts.items() if k in live}

    def _save(self):
        self._prune()
        self._store.async_delay_save(lambda: {'items': self.items, 'reviews': self.reviews[-MAX_REVIEWS:]}, 5)

    @callback
    def _changed(self, event):
        if not self.enabled:
            return
        eid = event.data.get('entity_id')
        new, old = event.data.get('new_state'), event.data.get('old_state')
        if new is None or old is None or new.state == old.state:
            return
        from .const import CONF_CAMSEC_ENTRIES, CONF_CAMSEC_SENSORS
        contacts = self.options.get(CONF_CAMSEC_ENTRIES) or []
        sensors = self.options.get(CONF_CAMSEC_SENSORS) or []
        if eid in contacts:
            self.add(eid, f'{new.name}: {new.state}', 'contact',
                     evidence='unknown' if new.state in ('unknown', 'unavailable') else 'observed')
        elif eid in sensors and new.state == 'on' and old.state not in ('unknown', 'unavailable'):
            from .camsecurity import camera_for
            self.add(camera_for(self.hass, eid) or eid, f'Person reported at {new.name}', 'detection',
                     read_source=eid)

    def add(self, source, text, kind, *, evidence='observed', when=None, event_id=None, read_source=None):
        if not self.enabled:
            return None
        now = when or dt_util.utcnow()
        self._prune()
        if now < dt_util.utcnow() - timedelta(days=7):
            return None
        if event_id:
            existing = next((item for item in self.items if any(
                e.get('event_id') == event_id and e.get('kind') == kind and e.get('source') == source
                for e in item['evidence'])), None)
            if existing is not None:
                return existing
        possible = []
        for item in self.items:
            start, end = dt_util.parse_datetime(item['start']), dt_util.parse_datetime(item['end'])
            if start - GAP <= now <= end + GAP:
                if kind == 'clip' and source not in item['sources']:
                    continue  # delayed clips must also match a known camera
                possible.append(item)
        item = possible[0] if len(possible) == 1 else None
        if item is None:
            item = {'id': uuid.uuid4().hex[:12], 'start': now.isoformat(), 'end': now.isoformat(),
                    'sources': [], 'evidence': [], 'alert_level': 0,
                    'link': 'Events close in time; not proof of one person or a confirmed route.'}
            self.items.insert(0, item)
        if event_id and any(e.get('event_id') == event_id and e['kind'] == kind for e in item['evidence']):
            return item
        item['start'] = min(item['start'], now.isoformat())
        item['end'] = max(item['end'], now.isoformat())
        if source not in item['sources']:
            item['sources'].append(source)
        row = {'time': now.isoformat(), 'source': source, 'kind': kind,
               'status': evidence, 'text': text[:600], 'event_id': event_id}
        if read_source:
            row['read_source'] = read_source
        if self.options.get(CONF_COVERAGE) and when is None and kind in ('contact', 'detection'):
            # Only take snapshots as live evidence arrives. A clip fetched later says nothing about a past outage.
            gaps = {}
            for camera in configured_cameras(self.hass, self.options):
                state = self.hass.states.get(camera)
                if not state or state.attributes.get('restored'):
                    gaps[camera] = 'unknown'
                elif state.state in ('unknown', 'unavailable'):
                    gaps[camera] = state.state
            if gaps:
                row['camera_gaps'] = gaps
        item['evidence'].append(row)
        item['evidence'] = item['evidence'][-50:]
        self._capture_prediction(item)
        self._save()
        return item

    def alert(self, sensor, text, reason):
        from .camsecurity import camera_for
        item = self.add(camera_for(self.hass, sensor) or sensor, text, 'alert', evidence='inferred', read_source=sensor)
        if item is None:
            return True, None
        level = ALERT_LEVELS.get(reason, 1)
        pending = self._pending_alerts.get(item['id'], set())
        if level <= max([item['alert_level'], *pending]):
            return False, item['id']
        self._pending_alerts.setdefault(item['id'], set()).add(level)  # reserve only work that will run
        return True, item['id']

    def finish_alert(self, incident_id, level, accepted):
        pending = self._pending_alerts.get(incident_id, set())
        pending.discard(level)
        if not pending:
            self._pending_alerts.pop(incident_id, None)
        item = next((i for i in self.items if i['id'] == incident_id), None)
        if accepted and item is not None:
            item['alert_level'] = max(item['alert_level'], level)
            self._capture_prediction(item)
            self._save()

    def _capture_prediction(self, item: dict) -> None:
        """Freeze the latest incident prediction when evidence/alert level changes.

        A later household review must never recompute an aged incident as resolved.
        Earlier rows without this snapshot are retained but cannot earn authority.
        """
        from .incident_reasoning import assess_incident

        now = dt_util.utcnow()
        legacy, _ = stage(item, now=now)
        core = investigate(item, assess_incident(item), baseline_stage=legacy, now=now)["stage"]
        item["decision_snapshot"] = {
            "version": 1, "at": now.isoformat(), "legacy": legacy, "core": core,
            "evidence_count": len(item.get("evidence") or []),
            "alert_level": item.get("alert_level", 0),
        }

    def review(self, incident_id: str, outcome: str, via: str | None = None) -> dict:
        """Attach explicit household review to a retained incident only.

        Human review is not independent ground truth. Never adjust alarms,
        detector thresholds, routes or downstream permissions.
        """
        from .outcome_learning import LABELS

        if not self.enabled:
            return {"error": "Incident review is disabled"}
        self._prune()
        if outcome not in LABELS and outcome != "clear":
            return {"error": "Unsupported incident review outcome"}
        row = next((i for i in self.items if i.get("id") == incident_id), None)
        if row is None:
            return {"error": "Incident is not retained"}
        self.reviews = [r for r in self.reviews if r.get("id") != incident_id]
        if outcome == "clear":
            row.pop("household_review", None)
        else:
            snapshot = row.get("decision_snapshot") or {}
            verified = snapshot.get("version") == 1 and isinstance(snapshot.get("core"), str) and isinstance(snapshot.get("legacy"), str)
            predicted = snapshot.get("legacy") if verified else None
            core = snapshot.get("core") if verified else None
            now = dt_util.utcnow().isoformat()
            row["household_review"] = {"label": outcome, "predicted_stage": predicted, "core_stage": core,
                                       "at": now, "source": "household_review_not_independent_proof",
                                       "prediction_recorded": verified}
            if via:
                row["household_review"]["via"] = via  # e.g. "not a person" on the security page
            self.reviews.append({"id": incident_id, "at": now, "label": outcome, "legacy": predicted, "core": core,
                                 "prediction_recorded": verified, "prediction_at": snapshot.get("at") if verified else None})
            self.reviews = self.reviews[-MAX_REVIEWS:]
        self._save()
        self._check_authority()
        return {"changed": True, "detector_settings_changed": False, "alert_policy_changed": False}

    # ----- the cognitive core deciding ---------------------------------------------------------
    def authority(self, scope: str = "incident_stage") -> dict:
        """Whether the cognitive core makes a decision now: switched on, and earned from the reviews."""
        status = authority(self.reviews, scope)
        on = bool(self.options.get(CONF_COGNITIVE_DECIDES)) and (
            scope == "incident_stage" or bool(self.options.get(CONF_COGNITIVE_HEADS_UP)))
        if scope == "night_heads_up":
            # Evidence-category reviews cannot verify who was at the gate.
            # Retain the review scores for visibility, but never grant this authority
            # without a separately tested and verified household identity signal.
            return {**status, "switched_on": on, "deciding": False,
                    "why": [*status["why"], "independently verified household identity is not available"]}
        return {**status, "switched_on": on, "deciding": on and status["earned"]}

    def quiet_heads_up(self, incident_id, reason) -> str | None:
        """Why the spoken night heads-up for this incident can be left unsaid, or None to say it.

        Only the spoken heads-up for someone seen at night (never away, over the fence or loitering), only when
        the core has earned this decision and is sure the incident is someone from the house. The alert itself —
        the timeline, the phones — is untouched."""
        if reason != "night" or not self.authority("night_heads_up")["deciding"]:
            return None
        # Contact timing and camera correlation cannot identify a household member.
        # Until an independently verified identity signal is supplied and tested here,
        # do not withhold any spoken security heads-up, even if historic reviews qualify.
        return None

    def _check_authority(self) -> None:
        status = self.authority()
        heads_up = self.authority("night_heads_up")
        if heads_up["switched_on"] and heads_up["deciding"] != getattr(self, "_heads_up_earned", None):
            self._heads_up_earned = heads_up["deciding"]
            if self.on_authority is not None:
                self.on_authority("The cognitive core now quietens night heads-ups it's sure are someone from the house."
                                  if heads_up["deciding"] else "The cognitive core won't quieten night heads-ups: "
                                  + "; ".join(heads_up["why"]) + ".")
        if status["earned"] == self._earned:
            return
        self._earned = status["earned"]
        if status["switched_on"] and self.on_authority is not None:
            self.on_authority("The cognitive core now decides the stage of security incidents: your reviews show it gets "
                              "each kind right." if status["earned"] else
                              "The cognitive core has stopped deciding incident stages: " + "; ".join(status["why"]) + ".")

    def stage_for(self, item, assessment):
        """(stage, why, decided by, the rules' stage) for an incident: the core's when it decides, else the rules'."""
        legacy, legacy_why = stage(item, assessment)
        if not self.authority()["deciding"]:
            return legacy, legacy_why, "rules", legacy
        core = investigate(item, assessment, baseline_stage=legacy)["stage"]
        final, by = decided_stage(legacy, core, True)
        return final, (legacy_why if final == legacy else "the cognitive core's assessment"), by, legacy

    def reviewed_outcomes(self) -> dict:
        """Evaluate labelled episodes without exporting source, faces or IDs."""
        from .outcome_learning import evaluate

        if not self.enabled:
            return {"enabled": False}
        self._prune()
        labelled = []
        for row in self.items:
            review = row.get("household_review")
            if not isinstance(review, dict) or review.get("label") is None:
                continue
            # Stage at review time is inferred from recorded evidence; stage matching
            # does not prove factual correctness or population detection accuracy.
            predicted = review.get("predicted_stage")
            if not isinstance(predicted, str) or review.get("prediction_recorded") is not True:
                continue
            labelled.append({"human_label": review["label"], "predicted_stage": predicted})
        return {**evaluate(labelled), "reviewed_total": len(labelled),
                "labels_independent_ground_truth": False}

    def recent(self, query=''):
        if not self.enabled:
            return []
        self._prune()
        query = query.lower().strip()
        from .incident_reasoning import assess_incident

        # Pick the eight first, then assess only those: this runs on every update of the Mind sensor.
        chosen = [i for i in self.items
                  if not query or query in (' '.join(i['sources']) + ' ' + ' '.join(e['text'] for e in i['evidence'])).lower()][:8]
        out = []
        for i in chosen:
            row = dict(i, evidence=[public_evidence(e) for e in i['evidence']], assessment=assess_incident(i))
            row["reviewed"] = isinstance(row.pop("household_review", None), dict)  # the label stays private
            # The original stage remains authoritative; the new coordinator
            # only compares decisions and tracks disagreement for later review.
            final, reason, by, legacy_stage = self.stage_for(i, row['assessment'])
            if self.options.get(CONF_COGNITIVE_SHADOW):
                row['cognitive_shadow'] = self.cognitive.incident(i, row['assessment'], legacy_stage)
            if self.options.get(CONF_STAGES, True):
                row['stage'], row['stage_why'], row['stage_by'] = final, reason, by
                if by != "rules":
                    row['rules_stage'] = legacy_stage
            out.append(row)
        return out

    def configure(self, options):
        # The guarded Control path holds a reference to this coordinator.
        # Never replace the object on an options update; clear its state only
        # on the enabled -> disabled transition, including when incidents are
        # disabled. This keeps Mind and action outcomes on the same counters.
        was_enabled = bool(self.options.get(CONF_COGNITIVE_SHADOW) and self.options.get(CONF_INCIDENTS))
        enabled = bool(options.get(CONF_COGNITIVE_SHADOW) and options.get(CONF_INCIDENTS))
        if was_enabled and not enabled:
            self.cognitive.reset()
        switched = bool(options.get(CONF_COGNITIVE_DECIDES)) != bool(self.options.get(CONF_COGNITIVE_DECIDES))
        self.options = options
        if switched and options.get(CONF_COGNITIVE_DECIDES) and self.on_authority is not None:
            status = self.authority()
            self.on_authority("The cognitive core now decides the stage of security incidents." if status["earned"] else
                              "The cognitive core will decide incident stages once it has earned it: "
                              + "; ".join(status["why"]) + ".")
        if not options.get(CONF_COVERAGE):
            had_gaps = False
            for item in self.items:
                for row in item['evidence']:
                    if row.pop("camera_gaps", None):
                        had_gaps = True
            if had_gaps:
                self._save()
