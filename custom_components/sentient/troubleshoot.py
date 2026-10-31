"""Read-only, bounded evidence for a conversational investigation; never infer causality."""
from __future__ import annotations

import re
from homeassistant.util import dt as dt_util
from .evidence import describe_state

_WORDS = re.compile(r"[a-z0-9]+")
_FILLER = set("""why did didn didn’t didnt not the a an it that this about tell me you
say said speak spoke announcement announcements alert alerts play played work working
happen happened happen what when where was were is are has have had do does
system health security check please my at to of for on off and then""".split())


def terms(text):
    return {w for w in _WORDS.findall(text.lower()) if len(w) > 2} - _FILLER


def report(hass, rt, what, hours):
    """Return source-labelled records, explicit gaps and one useful next question."""
    from datetime import timedelta

    now = dt_util.utcnow()
    cutoff = now - timedelta(hours=hours)
    query = terms(what)
    evidence = []
    entities = []
    for state in hass.states.async_all():
        if query and query & terms(state.entity_id + ' ' + state.name):
            entities.append(state.entity_id)
            if len(entities) == 12:
                break

    # Match subject words as whole tokens: "gate" must not match "aggregate".
    # A diagnostic query with no subject asks for clarification instead of dumping house logs.
    def matches(text):
        return bool(query and query & terms(text))

    def add(source, time, status, text, **extra):
        evidence.append(dict(id=f'e{len(evidence) + 1}', source=source, time=time,
                             status=status, text=str(text)[:600], **extra))

    for item in rt.timeline.since(hours, None, 400):
        if len(evidence) >= 20:
            break
        text = str(item.get('text', '')) + ' ' + str(item.get('detail', ''))
        if not matches(text):
            continue
        add('timeline', item.get('time'), item.get('evidence', 'recorded'), text,
            kind=item.get('kind'), reason=item.get('reason'),
            incident_id=item.get('incident_id'))

    incidents = getattr(rt, 'incidents', None)
    if incidents is not None and incidents.enabled:
        incidents._prune()
        for incident in incidents.items:
            for item in incident.get('evidence', []):
                if len(evidence) >= 35:
                    break
                stamp = dt_util.parse_datetime(item.get('time', ''))
                if stamp is None or stamp.tzinfo is None or not cutoff <= stamp <= now:
                    continue
                if matches(item.get('source', '') + ' ' + item.get('text', '')):
                    add('incident', item['time'], item.get('status', 'unknown'),
                        item.get('text', ''), kind=item.get('kind'), incident_id=incident['id'])
            if len(evidence) >= 35:
                break

    current = [describe_state(hass, eid) for eid in entities]
    failures = []
    for label, (when, error) in getattr(rt.speech, 'failures', {}).items():
        if cutoff <= when <= now:
            failures.append({'speaker': label, 'time': when.isoformat(), 'error': str(error)[:300],
                             'relation': 'Delivery failure in this window; not linked to the requested event.'})
        if len(failures) >= 8:
            break

    health = getattr(rt, 'health', None)
    health_rows = health.describe() if health is not None else []
    general_health = not query and bool({'system', 'health'} & set(_WORDS.findall(what.lower())))
    current_health = [line for line in health_rows if general_health or matches(line)][:12]

    held = [e for e in evidence if e.get('kind') == 'held']
    sent = [e for e in evidence if e.get('kind') == 'said']
    findings = []
    if held:
        findings.append({'status': 'recorded', 'evidence_ids': [e['id'] for e in held],
                         'finding': 'Matching messages were held back; their recorded reasons apply to those messages only.'})
    if sent:
        findings.append({'status': 'recorded', 'evidence_ids': [e['id'] for e in sent],
                         'finding': 'Matching speech was logged as sent; this does not prove it was audible.'})
    unknowns = ['Nearby timestamps and matching words do not establish a shared event or cause.',
                'Current device state does not establish its state at the time of an earlier event.',
                'Only retained Sentient records are searched; missing records do not prove nothing happened.']
    if not evidence:
        unknowns.append('No matching historical evidence was found in this window.')
    if not query:
        question = 'Which device or announcement are you asking about?'
    elif not evidence:
        question = 'Roughly when did you expect this to happen?'
    elif len(held) + len(sent) > 1:
        question = 'Which time or occurrence do you mean?'
    elif held:
        question = 'Is that the announcement you meant?'
    elif sent:
        question = 'Which speaker did you expect to hear it on?'
    else:
        question = 'What did you expect to happen after that event?'
    return {'query': what[:300], 'window': {'from': cutoff.isoformat(), 'to': now.isoformat()},
            'evidence': evidence, 'current_readings': current, 'delivery_failures': failures,
            'current_health': {'issues': current_health,
                               'note': 'Latest cached health report, not a fresh probe or proof of a past fault.'},
            'findings': findings, 'unknowns': unknowns, 'next_question': question,
            'instruction': 'Explain only supported findings, distinguish observations from inferences, and ask at most the next question. Do not change devices or settings.'}
