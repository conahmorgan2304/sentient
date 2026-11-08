"""Restore is additive; settings proposals and goal updates must still be current."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.sentient.backup import export_all, import_all, AutoBackup
from custom_components.sentient.learning import Routine
from custom_components.sentient.goals import Goals, MAX_OPEN
from tests.test_routines_backup import _setup


@pytest.mark.parametrize('section,value', [('memories', {}), ('spots', [1]), ('routines', ['bad']),
    ('shushed', 'bad'), ('learned_choices', [None]), ('lists', []), ('motion_lights', {'rules': 'bad'}),
    ('settings', []), ('deleted_routines', [])])
async def test_malformed_backup_sections_fail_before_any_import(hass, section, value):
    entry = await _setup(hass)
    data = {'sentient_backup': 1, 'memories': [{'text': 'A new fact'}], section: value}
    with pytest.raises(ValueError): import_all(hass, entry, data)
    assert not entry.runtime_data.memory.items


async def test_restore_preserves_existing_routines_spots_and_night_settings(hass):
    entry = await _setup(hass)
    rt = entry.runtime_data
    routine = rt.shortcuts.upsert({'name': 'Evening', 'say': 'Current reply'})
    spot = rt.spots.set('camera.front', 'Drive', [.1, .1, .6, .6], aliases='driveway')
    before = deepcopy(spot)
    hours = (rt.motion_lights.night_start, rt.motion_lights.night_end)
    data = {'sentient_backup': 1, 'routines': [{'name': 'Evening', 'say': 'Old reply'}],
        'spots': [{'name': 'Drive', 'camera': 'camera.other', 'box': [.2, .2, .8, .8]}],
        'motion_lights': {'night_start': '01:00:00', 'night_end': '02:00:00'}}
    counts = import_all(hass, entry, data)
    assert routine.say == 'Current reply' and rt.spots.get('camera.front', 'Drive') == before
    assert (rt.motion_lights.night_start, rt.motion_lights.night_end) == hours
    assert counts['routines'] == counts['spots'] == 0


async def test_spot_polygon_and_aliases_survive_restore(hass):
    entry = await _setup(hass)
    points = [[.1, .1], [.8, .2], [.4, .8]]
    entry.runtime_data.spots.set('camera.front', 'Drive', [.1, .1, .8, .8], points, 'driveway')
    data = export_all(hass, entry)
    entry.runtime_data.spots.spots.clear()
    import_all(hass, entry, data)
    restored = entry.runtime_data.spots.get('camera.front', 'Drive')
    assert restored['points'] == points and restored['aliases'] == 'driveway'


async def test_backup_snapshot_does_not_change_with_live_data(hass):
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.spots.set('camera.front', 'Drive', [.1, .1, .8, .8])
    rt.mutes.add('kettle')
    data = export_all(hass, entry)
    rt.spots.spots['drive']['aliases'] = 'New live alias'
    rt.mutes.items[0]['what'] = 'another thing'
    assert data['spots'][0]['aliases'] == '' and data['shushed'][0]['what'] == 'kettle'


async def test_restore_does_not_drop_current_routine_from_old_deleted_list(hass):
    entry = await _setup(hass)
    learner = entry.runtime_data.learner
    current = Routine('light.lamp', 'lamp', 'on', 1200, 'every day', .8, 10, 4, adopted=True)
    learner.routines[current.id] = current
    import_all(hass, entry, {'sentient_backup': 1, 'deleted_routines': {current.id: 'Old deleted plan'}})
    assert learner.get(current.id) is current and current.adopted and current.id not in learner.dismissed


@pytest.mark.parametrize('change', ['clock', 'legacy', 'invalid_flags', 'unchanged'])
async def test_restored_adoption_needs_the_exact_exported_plan(hass, change):
    entry = await _setup(hass)
    learner = entry.runtime_data.learner
    current = Routine('light.lamp', 'lamp', 'on', 1200, 'every day', .8, 10, 4, adopted=True)
    learner.routines[current.id] = current
    data = export_all(hass, entry)
    current.adopted = False
    if change == 'clock': current.minute += 45
    if change == 'legacy': data['learned_choices'][0].pop('plan')
    if change == 'invalid_flags': data['learned_choices'][0]['adopted'] = 'false'
    import_all(hass, entry, data)
    assert current.adopted == (change == 'unchanged')


async def test_restore_memory_capacity_id_collisions_and_invalid_metadata(hass):
    entry = await _setup(hass)
    memory = entry.runtime_data.memory
    existing = memory.add('A pinned current fact')
    memory.pin(existing.id)
    data = {'sentient_backup': 1, 'memories': [{'id': existing.id, 'text': 'Different imported fact'},
        {'text': 'Bad usage metadata', 'hits': 'many'}, {'text': 'A new valid fact'}]}
    with patch('custom_components.sentient.backup.MEMORY_MAX_ITEMS', 1):
        counts = import_all(hass, entry, data)
    assert memory.items == [existing] and existing.pinned
    assert counts['skipped'] == 3


async def test_import_lists_uses_exact_names_not_partial_matches(hass):
    entry = await _setup(hass)
    entry.runtime_data.lists['checks'].upsert({'name': 'Door'})
    data = {'sentient_backup': 1, 'lists': {'checks': [{'name': 'Front Door'}, {'name': 'Front Door'}]}}
    counts = import_all(hass, entry, data)
    assert counts['checks'] == 1
    assert len(entry.runtime_data.lists['checks'].items) == 2


async def test_imported_mutes_get_unique_ids_and_utc_times(hass):
    entry = await _setup(hass)
    original = entry.runtime_data.mutes.add('kettle')
    data = {'sentient_backup': 1, 'shushed': [{'id': original['id'], 'kind': 'words', 'what': 'WASHER',
        'until': (dt_util.utcnow() + timedelta(hours=1)).isoformat()}]}
    import_all(hass, entry, data)
    restored = entry.runtime_data.mutes.items[-1]
    assert restored['id'] != original['id'] and restored['what'] == 'washer'
    assert entry.runtime_data.mutes.text_muted('The washer is finished')


async def test_invalid_import_settings_fail_before_mutating_memory(hass):
    entry = await _setup(hass)
    with pytest.raises(ValueError):
        import_all(hass, entry, {'sentient_backup': 1, 'memories': [{'text': 'New fact'}],
            'settings': {'preheat': 'not a boolean'}}, settings=True)
    assert not entry.runtime_data.memory.items


async def test_valid_selected_settings_restore_still_works(hass):
    entry = await _setup(hass)
    counts = import_all(hass, entry, {'sentient_backup': 1, 'settings': {'persona': 'New name', 'preheat': False}}, settings=True)
    assert counts['settings'] == 1 and entry.options['preheat'] is False and entry.options['persona'] == 'New name'
    await hass.async_block_till_done()


async def test_same_instant_backups_have_distinct_names_and_keep_the_newest(hass, tmp_path, freezer):
    entry = await _setup(hass)
    backup = AutoBackup(hass, entry, {'backup_keep': 1})
    with patch.object(AutoBackup, 'folder', new_callable=lambda: property(lambda self: tmp_path)):
        first, second = await asyncio.gather(backup.async_save(), backup.async_save())
        assert first != second and (tmp_path / second).exists()
        assert not (tmp_path / first).exists()
        assert len(await backup.async_list()) == 1


async def test_backup_restore_does_not_follow_a_symlink_outside_folder(hass, tmp_path):
    entry = await _setup(hass)
    folder = tmp_path / 'backups'
    folder.mkdir()
    outside = tmp_path / 'outside.json'
    outside.write_text('{"sentient_backup": 1}')
    (folder / 'sentient-link.json').symlink_to(outside)
    with patch.object(AutoBackup, 'folder', new_callable=lambda: property(lambda self: folder)):
        with pytest.raises(ValueError, match='outside'):
            await AutoBackup(hass, entry, {}).async_restore('sentient-link.json')


async def test_disabled_queued_automatic_backup_does_no_write(hass, tmp_path):
    entry = await _setup(hass)
    with patch.object(AutoBackup, 'folder', new_callable=lambda: property(lambda self: tmp_path)):
        assert await AutoBackup(hass, entry, {}).async_save(force=False) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('limit', [1, 2, 3, 9, 4990, 5000])
async def test_ai_limit_proposals_cannot_round_to_unlimited_or_exceed_ui_bound(hass, limit):
    from tests.test_proposals import _setup as setup
    entry = await setup(hass, daily_ai_limit=limit)
    props = entry.runtime_data.proposals
    candidates = props.candidates([{'kind': 'ai', 'about': 'limit', 'text': 'Limit reached'}])
    if limit == 5000:
        assert not candidates
    else:
        assert limit < candidates[0]['value'] <= 5000


@pytest.mark.parametrize('change', ['disabled', 'stale', 'cleared', 'expired', 'invalid_value'])
async def test_old_proposal_cannot_apply_after_its_context_changes(hass, freezer, change):
    from tests.test_proposals import _setup as setup, REPEAT
    entry = await setup(hass, remark_repeat_hours=6)
    props = entry.runtime_data.proposals
    props.refresh([REPEAT])
    if change == 'disabled': hass.config_entries.async_update_entry(entry, options={'proposals': False})
    if change == 'stale': hass.config_entries.async_update_entry(entry, options={'remark_repeat_hours': 18})
    if change == 'cleared': hass.config_entries.async_update_entry(entry, data={k:v for k,v in entry.data.items() if k != 'remark_repeat_hours'})
    if change == 'expired': freezer.tick(timedelta(days=8))
    if change == 'invalid_value': props.open[0]['value'] = 0
    before = dict(entry.options)
    assert 'error' in props.answer('repeat', True)
    assert entry.options == before
    await hass.async_block_till_done()


@pytest.fixture
def goals(hass):
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0)
    rt = SimpleNamespace(speech=speech, timeline=Mock())
    obj = Goals(hass, 'review', {'goals': True}, lambda: rt)
    obj._save = Mock()
    hass.states.async_set('sensor.energy', '3')
    return obj


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', float('nan'), float('inf')])
async def test_nonfinite_goal_values_do_not_crash_progress_or_poison_baseline(goals, value):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-01', value)])):
        await goals.async_set('Use less energy', 'sensor.energy')
        await goals.async_check()
    assert goals.items[0]['baseline'] is None and goals.listing()[0]['progress'] is None


@pytest.mark.parametrize('change', ['disabled', 'settings', 'privacy', 'stopped'])
async def test_late_goal_creation_does_not_publish_after_policy_change(goals, change):
    async def history(*args):
        if change == 'disabled': goals.options['goals'] = False
        if change == 'settings': goals.options = dict(goals.options)
        if change == 'privacy': goals._rt().speech._privacy_generation += 1
        if change == 'stopped': goals.async_stop()
        return [('2026-11-01', 5)]
    with patch('custom_components.sentient.goals.daily_values', side_effect=history):
        assert 'error' in await goals.async_set('Use less', 'sensor.energy')
    assert not goals.items
    goals._rt().timeline.add.assert_not_called()


async def test_concurrent_goal_creation_obeys_open_limit(goals):
    for i in range(MAX_OPEN - 1): await goals.async_set(f'Goal {i}')
    entered, release = asyncio.Event(), asyncio.Event()
    async def history(*args):
        entered.set()
        await release.wait()
        return []
    with patch('custom_components.sentient.goals.daily_values', side_effect=history):
        one = asyncio.create_task(goals.async_set('One', 'sensor.energy'))
        await entered.wait()
        two = asyncio.create_task(goals.async_set('Two', 'sensor.energy'))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(one, two)
    assert len(goals.open()) == MAX_OPEN


async def test_closed_goal_does_not_receive_late_history(goals):
    await goals.async_set('Use less')
    goal = goals.items[0]
    goal['entity_id'] = 'sensor.energy'
    async def history(*args):
        goals.close(goal['id'])
        return [('2026-11-01', 4)]
    with patch('custom_components.sentient.goals.daily_values', side_effect=history):
        await goals.async_check()
    assert goal['closed'] and not goal['checks']


async def test_empty_or_ambiguous_goal_close_does_not_pick_one(goals):
    await goals.async_set('Heating downstairs')
    await goals.async_set('Heating upstairs')
    assert goals.close('') is None and goals.close('heating') is None
    assert len(goals.open()) == 2


@pytest.mark.parametrize('job', ['digest', 'briefing'])
@pytest.mark.parametrize('change', ['privacy', 'settings', 'stopped', 'guest'])
async def test_late_summary_work_does_not_publish_old_context(hass, job, change):
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.timeline.add('said', 'A private earlier observation')
    async def ai(*args):
        if change == 'privacy': rt.speech._privacy_generation += 1
        if change == 'settings': rt.options = dict(rt.options)
        if change == 'stopped': rt.upkeep.async_stop()
        if change == 'guest': rt.speech.guest_mode = True
        return 'Old private summary'
    with patch.object(rt.upkeep, '_ai', side_effect=ai), patch.object(rt.speech, 'async_say', new=AsyncMock()) as say:
        result = await (rt.upkeep.async_digest() if job == 'digest' else rt.upkeep.async_speak_briefing())
    say.assert_not_awaited()
    assert result.get('skipped') or result.get('reason') == 'briefing_changed'
    assert not any(i['text'] == 'Old private summary' for i in rt.timeline.items)


async def test_briefing_consumes_only_the_thoughts_it_actually_included(hass):
    from custom_components.sentient.speech import SpeechResult
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.followyou.keep('First thought', source='thought')
    async def ai(*args):
        rt.followyou.keep('Newer thought', source='thought')
        return 'The first thought.'
    with patch('custom_components.sentient.upkeep.gather_briefing', new=AsyncMock(return_value={'overnight_thoughts': ['First thought']})), patch.object(rt.upkeep, '_ai', side_effect=ai), patch.object(rt.speech, 'async_say', new=AsyncMock(return_value=SpeechResult(True, 'spoken', 'The first thought.', []))):
        await rt.upkeep.async_speak_briefing()
    assert [q['text'] for q in rt.followyou.queue] == ['Newer thought']


async def test_optional_null_entity_selection_can_be_restored(hass):
    entry = await _setup(hass)
    data = export_all(hass, entry)
    data['settings']['ai_task_entity'] = None
    result = import_all(hass, entry, data, settings=True)
    assert result['settings'] == 1 and entry.options['ai_task_entity'] is None
    await hass.async_block_till_done()
