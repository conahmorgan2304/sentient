"""Daily goals must compare reset-aware totals and calendar-day scalar observations."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.sentient.goal_statistics import boundaries, _history, _native, daily_values, VERSION
from custom_components.sentient.goals import Goals

EID = 'sensor.goal'


@pytest.fixture(autouse=True)
def utc_zone():
    before = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(ZoneInfo('UTC'))
    yield
    dt_util.set_default_time_zone(before)


def at(day=8, hour=0, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo('UTC'))


def reading(value, when, kind=None, unit=None, **attrs):
    return State(EID, str(value), {'state_class': kind, 'unit_of_measurement': unit, **attrs},
        last_changed=when, last_updated=when)


def test_total_increasing_counts_each_observed_reset():
    bounds = boundaries(at(9), 1)
    rows = [reading(v, at(8, h), 'total_increasing', 'kWh') for h, v in ((0, 100), (6, 104), (12, 0), (18, 3))]
    assert _history(rows, bounds, 'total_increasing', 'kWh') == {'2026-11-08': 7}


def test_signed_total_is_net_change_not_highest_minus_lowest():
    rows = [reading(v, at(8, h), 'total', 'kWh') for h, v in ((0, 100), (6, 110), (12, 105), (18, 108))]
    assert _history(rows, boundaries(at(9), 1), 'total', 'kWh') == {'2026-11-08': 8}


def test_declared_total_reset_uses_new_counter_value():
    rows = [reading(100, at(8), 'total', 'kWh', last_reset='old'),
            reading(104, at(8, 6), 'total', 'kWh', last_reset='old'),
            reading(2, at(8, 12), 'total', 'kWh', last_reset='new')]
    assert _history(rows, boundaries(at(9), 1), 'total', 'kWh') == {'2026-11-08': 6}


def test_gauge_average_uses_duration_not_number_of_reports():
    rows = [reading(10, at(8))] + [reading(30, at(8, 23, minute)) for minute in range(60)]
    result = _history(rows, boundaries(at(9), 1), None, None)
    assert result['2026-11-08'] == pytest.approx((23 * 10 + 30) / 24)


def test_start_state_is_carried_into_requested_day_only():
    rows = [reading(10, at(1)), reading(20, at(8, 12))]
    assert _history(rows, boundaries(at(9), 1), None, None) == {'2026-11-08': 15}


def test_constant_counter_yields_zero_observed_change_not_missing():
    rows = [reading(100, at(1), 'total_increasing', 'kWh')]
    assert _history(rows, boundaries(at(9), 2), 'total_increasing', 'kWh') == {'2026-11-07': 0, '2026-11-08': 0}


@pytest.mark.parametrize('bad', ['unknown', 'unavailable', 'nan', 'inf', '-inf'])
@pytest.mark.parametrize('kind', [None, 'total_increasing'])
def test_missing_interval_is_not_treated_as_zero_or_complete_day(bad, kind):
    rows = [reading(10, at(8), kind), reading(bad, at(8, 6), kind), reading(20, at(8, 12), kind)]
    assert not _history(rows, boundaries(at(9), 1), kind, None)


def test_unit_changes_within_history_invalidate_that_day():
    rows = [reading(10, at(8), 'measurement', '°C'), reading(68, at(8, 12), 'measurement', '°F')]
    assert not _history(rows, boundaries(at(9), 1), 'measurement', '°C')


def test_missing_initial_half_day_is_not_a_full_day_average():
    assert not _history([reading(20, at(8, 12))], boundaries(at(9), 1), None, None)


def test_current_day_and_end_boundary_samples_are_excluded():
    rows = [reading(10, at(8)), reading(999, at(9))]
    assert _history(rows, boundaries(at(9), 1), None, None) == {'2026-11-08': 10}


@pytest.mark.parametrize('date,expected_hours', [(datetime(2026, 3, 30, tzinfo=ZoneInfo('Europe/London')), 23),
    (datetime(2026, 10, 26, tzinfo=ZoneInfo('Europe/London')), 25)])
def test_local_calendar_day_duration_handles_clock_changes(date, expected_hours):
    dt_util.set_default_time_zone(ZoneInfo('Europe/London'))
    bounds = boundaries(date, 1)
    assert (bounds[1] - bounds[0]).total_seconds() / 3600 == expected_hours
    midpoint = bounds[0] + timedelta(hours=1)
    result = _history([reading(10, bounds[0]), reading(20, midpoint)], bounds, None, None)
    assert next(iter(result.values())) == pytest.approx((10 + 20 * (expected_hours - 1)) / expected_hours)


def test_native_reset_adjusted_daily_changes_take_precedence(hass):
    bounds = boundaries(at(9), 1)
    meta = {EID: (1, {'has_sum': True, 'unit_class': 'energy', 'unit_of_measurement': 'kWh'})}
    rows = {EID: [{'start': bounds[0].timestamp(), 'end': bounds[1].timestamp(), 'change': 7}]}
    with patch('homeassistant.components.recorder.statistics.get_metadata', return_value=meta), patch(
        'homeassistant.components.recorder.statistics.statistics_during_period', return_value=rows) as stats:
        assert _native(hass, EID, bounds, 'total_increasing', 'kWh') == {'2026-11-08': 7}
    args = stats.call_args.args
    assert args[3] == {EID} and args[4] == 'day' and args[-1] == {'change'}
    assert args[2] == bounds[-1] - timedelta(microseconds=1)


def test_native_unit_conversion_is_requested_explicitly(hass):
    meta = {EID: (1, {'has_sum': True, 'unit_class': 'energy', 'unit_of_measurement': 'kWh'})}
    with patch('homeassistant.components.recorder.statistics.get_metadata', return_value=meta), patch(
        'homeassistant.components.recorder.statistics.statistics_during_period', return_value={}) as stats:
        _native(hass, EID, boundaries(at(9), 1), 'total_increasing', 'Wh')
    assert stats.call_args.args[-2] == {'energy': 'Wh'}


@pytest.mark.parametrize('value,end', [(float('nan'), at(9)), (-1, at(9)), (4, at(10))])
def test_invalid_or_incomplete_native_days_are_not_accepted(hass, value, end):
    bounds = boundaries(at(9), 1)
    meta = {EID: (1, {'has_sum': True, 'unit_class': 'energy', 'unit_of_measurement': 'kWh'})}
    with patch('homeassistant.components.recorder.statistics.get_metadata', return_value=meta), patch(
        'homeassistant.components.recorder.statistics.statistics_during_period', return_value={EID:[{'start': at(8), 'end': end, 'change': value}]}):
        assert not _native(hass, EID, bounds, 'total_increasing', 'kWh')


async def test_daily_values_uses_raw_history_for_missing_statistics(hass):
    hass.config.components.add('recorder')
    hass.states.async_set(EID, '20', {'unit_of_measurement': '°C'})
    async def executor(fn, *args): return fn(*args)
    recorder = SimpleNamespace(async_add_executor_job=executor)
    with patch('homeassistant.components.recorder.get_instance', return_value=recorder), patch(
        'homeassistant.components.recorder.statistics.get_metadata', return_value={}), patch(
        'homeassistant.components.recorder.history.get_significant_states', return_value={EID: [reading(20, at(8), unit='°C')]}):
        assert await daily_values(hass, EID, 1, at(9)) == [('2026-11-08', 20)]


@pytest.fixture
def goals(hass, freezer):
    freezer.move_to(at(9, 12))
    hass.states.async_set(EID, '8', {'state_class': 'total_increasing', 'unit_of_measurement': 'kWh'})
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0)
    rt = SimpleNamespace(speech=speech, timeline=Mock())
    obj = Goals(hass, 'stats-review', {'goals': True}, lambda: rt)
    obj._save = Mock()
    return obj


def legacy_goal(obj):
    item = {'id':'old', 'title':'Use less energy', 'entity_id':EID, 'aim':'lower', 'created':at(1).isoformat(),
        'ends':datetime(2026, 12, 1, tzinfo=ZoneInfo('UTC')).isoformat(), 'baseline':999,
        'checks':[{'day':'2026-11-07', 'value':900}], 'closed':None}
    obj.items.append(item)
    return item


async def test_legacy_goals_rebuild_baseline_and_checks_without_mixing_methods(goals):
    item = legacy_goal(goals)
    async def values(*args, **kwargs):
        return [('2026-10-31',10)] if kwargs.get('end') else [('2026-10-31',999), ('2026-11-01',8), ('2026-11-08',6), ('2026-11-09',999)]
    with patch('custom_components.sentient.goals.daily_values', side_effect=values):
        await goals.async_check()
    assert item['baseline'] == 10 and item['statistics_version'] == VERSION
    assert item['checks'] == [{'day':'2026-11-01', 'value':8}, {'day':'2026-11-08', 'value':6}]
    assert goals.progress(item).startswith('30% lower than usual across 2 recorded days')


async def test_unrecoverable_legacy_baseline_is_not_replaced_with_post_goal_data(goals):
    item = legacy_goal(goals)
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[])):
        await goals.async_check()
    assert item['baseline'] is None and not item['checks'] and goals.progress(item) is None
    assert 'baseline' in item['statistics_note']


async def test_changed_units_pause_without_reading_or_comparing_old_values(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',10)])):
        await goals.async_set('Use less', EID)
    item = goals.items[0]
    goals.hass.states.async_set(EID, '8000', {'state_class':'total_increasing', 'unit_of_measurement':'Wh'})
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock()) as values:
        await goals.async_check()
    values.assert_not_awaited()
    assert item['needs_rebaseline'] and goals.listing()[0]['progress'] is None


async def test_expired_goal_still_closes_when_recorder_fails(goals):
    item = legacy_goal(goals)
    item['ends'] = at(8).isoformat()
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(side_effect=RuntimeError('database unavailable'))):
        await goals.async_check()
    assert item['closed'] and 'unavailable' in item['statistics_note']


async def test_legacy_migration_stops_after_privacy_changes_during_baseline_read(goals):
    item = legacy_goal(goals)
    async def values(*args, **kwargs):
        goals._rt().speech._privacy_generation += 1
        return [('2026-10-31',10)]
    with patch('custom_components.sentient.goals.daily_values', side_effect=values) as history:
        await goals.async_check()
    history.assert_awaited_once()
    assert item['baseline'] == 999 and 'statistics_version' not in item


async def test_small_nonzero_baseline_is_not_rounded_to_zero(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',.0005)])):
        out = await goals.async_set('Tiny use', EID)
    assert out['usual_per_day'] == .0005


def test_native_mean_uses_the_recorders_daily_measurement_statistic(hass):
    bounds = boundaries(at(9), 1)
    meta = {EID: (1, {'has_sum': False, 'has_mean': True, 'unit_class': 'temperature', 'unit_of_measurement': '°C'})}
    with patch('homeassistant.components.recorder.statistics.get_metadata', return_value=meta), patch(
        'homeassistant.components.recorder.statistics.statistics_during_period', return_value={EID:[{'start': at(8), 'end': at(9), 'mean': 18.4}]}) as stats:
        assert _native(hass, EID, bounds, 'measurement', '°C') == {'2026-11-08': 18.4}
    assert stats.call_args.args[-1] == {'mean'}


def test_native_unconvertible_units_do_not_query_mismatched_statistics(hass):
    meta = {EID: (1, {'has_sum': True, 'unit_class': None, 'unit_of_measurement': 'old-unit'})}
    with patch('homeassistant.components.recorder.statistics.get_metadata', return_value=meta), patch(
        'homeassistant.components.recorder.statistics.statistics_during_period') as stats:
        assert not _native(hass, EID, boundaries(at(9), 1), 'total', 'new-unit')
    stats.assert_not_called()


def test_half_hour_timezone_uses_its_own_midnight():
    dt_util.set_default_time_zone(ZoneInfo('Asia/Kolkata'))
    bounds = boundaries(datetime(2026, 11, 9, tzinfo=ZoneInfo('Asia/Kolkata')), 1)
    assert bounds[0].minute == 30 and bounds[0].hour == 18
    assert _history([reading(10, bounds[0])], bounds, None, None) == {'2026-11-08': 10}


async def test_complete_native_statistics_avoid_a_second_raw_history_read(hass):
    hass.config.components.add('recorder')
    hass.states.async_set(EID, '100', {'state_class':'total_increasing', 'unit_of_measurement':'kWh'})
    async def executor(fn, *args): return fn(*args)
    with patch('homeassistant.components.recorder.get_instance', return_value=SimpleNamespace(async_add_executor_job=executor)), patch(
        'custom_components.sentient.goal_statistics._native', return_value={'2026-11-08':7}), patch(
        'homeassistant.components.recorder.history.get_significant_states') as raw:
        assert await daily_values(hass, EID, 1, at(9)) == [('2026-11-08',7)]
    raw.assert_not_called()


async def test_native_statistics_override_only_the_matching_fallback_day(hass):
    hass.config.components.add('recorder')
    hass.states.async_set(EID, '100', {'state_class':'total_increasing', 'unit_of_measurement':'kWh'})
    async def executor(fn, *args): return fn(*args)
    rows = [reading(100, at(7), 'total_increasing', 'kWh'), reading(104, at(7,12), 'total_increasing','kWh'),
            reading(107, at(8,12), 'total_increasing','kWh')]
    with patch('homeassistant.components.recorder.get_instance', return_value=SimpleNamespace(async_add_executor_job=executor)), patch(
        'custom_components.sentient.goal_statistics._native', return_value={'2026-11-08':2}), patch(
        'homeassistant.components.recorder.history.get_significant_states', return_value={EID:rows}):
        assert await daily_values(hass, EID, 2, at(9)) == [('2026-11-07',4), ('2026-11-08',2)]


async def test_goal_day_before_creation_does_not_become_progress(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',10)])):
        await goals.async_set('Use less', EID)
        await goals.async_check()
    assert not goals.items[0]['checks']
    assert 'No completed goal day' in goals.items[0]['statistics_note']


async def test_goal_does_not_record_data_after_its_end(goals):
    goal = legacy_goal(goals)
    goal['ends'] = at(5,12).isoformat()
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(side_effect=[
        [('2026-10-31',10)], [('2026-11-04',8), ('2026-11-05',1), ('2026-11-08',1)]])):
        await goals.async_check()
    assert goal['checks'] == [{'day':'2026-11-04','value':8}]
    assert goal['closed']


async def test_zero_baseline_is_explained_instead_of_dividing(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',0)])):
        await goals.async_set('Use less', EID)
        await goals.async_check()
    assert goals.progress(goals.items[0]) is None
    assert 'Baseline is zero' in goals.items[0]['statistics_note']


async def test_baseline_exposes_number_of_comparable_days(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',10),('2026-11-07',8)])):
        result = await goals.async_set('Use less', EID)
    assert result['baseline_days'] == 2 and goals.listing()[0]['baseline_days'] == 2


async def test_one_failed_goal_read_does_not_block_another_goal(goals):
    first = legacy_goal(goals)
    second = dict(first, id='other', title='Other goal', entity_id=None)
    goals.items.append(second)
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(side_effect=RuntimeError('offline'))):
        await goals.async_check()
    assert first['statistics_note'].startswith('Daily history is unavailable')
    assert second['statistics_version'] == VERSION


async def test_listing_does_not_relabel_old_baseline_with_new_unit(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',10)])):
        await goals.async_set('Use less', EID)
    goals.hass.states.async_set(EID, '8000', {'state_class':'total_increasing', 'unit_of_measurement':'Wh'})
    row = goals.listing()[0]
    assert row['usual'] is None and row['progress'] is None
    assert row['baseline_unit'] == 'kWh' and row['unit'] == 'Wh' and row['statistics_note']


async def test_closing_after_unit_change_cannot_report_an_old_percentage(goals):
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(return_value=[('2026-11-08',10)])):
        await goals.async_set('Use less', EID)
    item = goals.items[0]
    item['checks'] = [{'day':'2026-11-08', 'value':8}]
    goals.hass.states.async_set(EID, '8000', {'state_class':'total_increasing', 'unit_of_measurement':'Wh'})
    closed = goals.close(item['id'])
    assert goals.progress(closed) is None and closed['statistics_note']


async def test_overdue_goal_reads_its_own_final_days_not_post_goal_history(goals):
    goal = legacy_goal(goals)
    goal['ends'] = at(5,12).isoformat()
    with patch('custom_components.sentient.goals.daily_values', new=AsyncMock(side_effect=[
        [('2026-10-31',10)], [('2026-11-04',8)]])) as history:
        await goals.async_check()
    assert history.await_args_list[1].args[2] == 4
    assert history.await_args_list[1].args[3] == at(5,12)
    assert goal['checks'] == [{'day':'2026-11-04','value':8}] and goal['closed']
