"""Learning must preserve meaning, pinned facts and the user's latest changes."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.sentient.memory import MemoryStore
from custom_components.sentient.reflection import Reflection
from custom_components.sentient.curiosity import Curiosity
from custom_components.sentient.upkeep import Upkeep


@pytest.fixture
def memory(hass):
    store = MemoryStore(hass, 'learning-quality')
    store._save = Mock()
    return store


@pytest.mark.parametrize('a,b', [
    ('The kitchen ceiling light should stay on overnight', 'The kitchen ceiling light should not stay on overnight'),
    ('The kitchen ceiling light should stay on overnight', 'The kitchen ceiling light should stay off overnight'),
    ('The bedroom temperature should stay at 20 degrees at night', 'The bedroom temperature should stay at 21 degrees at night'),
    ('Alice always visits Bob at home on Sunday', 'Bob always visits Alice at home on Sunday'),
    ('The kitchen light stays on during the evening', 'The bedroom light stays on during the evening'),
])
def test_similar_but_different_facts_do_not_silently_overwrite(memory, a, b):
    first = memory.add(a, source='service')
    second = memory.add(b, source='reflection')
    assert first.id != second.id and first.text == a
    assert len(memory.items) == 2


def test_equivalent_wording_reuses_id_without_refreshing_age_or_pinned_content(memory, freezer):
    first = memory.add('The rabbits live in the hutch by the shed.', source='service')
    memory.pin(first.id)
    created = first.created
    freezer.tick(timedelta(days=2))
    repeated = memory.add('Rabbits live in a hutch by the shed!', source='reflection')
    assert repeated is first and repeated.text == 'The rabbits live in the hutch by the shed.'
    assert repeated.created == created and repeated.source == 'service' and repeated.pinned


def test_explicit_confirmation_upgrades_inferred_source(memory):
    inferred = memory.add('The bins go out on Thursday', source='reflection')
    assert memory.add('The bins go out on Thursday.', source='service') is inferred
    assert inferred.source == 'service'


def test_corrections_and_facts_keep_separate_categories(memory):
    memory.add('The big light means the ceiling light', category='fact')
    memory.add('The big light means the ceiling light', category='correction')
    assert len(memory.items) == 2
    assert {m.category for m in memory.items} == {'fact', 'correction'}


@pytest.mark.parametrize('text,query', [
    ('A fűtés esténként alacsonyabb', 'fűtés'), ('Спальня должна быть прохладной', 'Спальня'),
    ('المطبخ دافئ في المساء', 'المطبخ'), ('厨房晚上保持温暖', '厨房晚上保持温暖'),
    ('Lämpötila on matala illalla', 'Lämpötila'),
])
def test_memory_recall_handles_unicode_words(memory, text, query):
    item = memory.add(text)
    assert memory.search(query) == [item]


@pytest.mark.parametrize('query', ['', 'the', 'a', 'on', '!!!'])
def test_empty_or_stopword_forget_cannot_delete_an_unrelated_memory(memory, query):
    item = memory.add('The rabbits live by the shed')
    assert memory.forget(query) is None
    assert memory.get(item.id) is item


def test_full_store_evicts_only_unpinned_and_preserves_chronological_order(memory):
    with patch('custom_components.sentient.memory.MEMORY_MAX_ITEMS', 3):
        pinned = memory.add('Pinned fact', category='note')
        memory.pin(pinned.id)
        drop = memory.add('Disposable note', category='note')
        other = memory.add('Another fact')
        newest = memory.add('A newly learned fact')
    assert memory.items == [pinned, other, newest]
    assert memory.get(drop.id) is None


def test_all_pinned_store_refuses_new_fact_without_losing_anything(memory):
    with patch('custom_components.sentient.memory.MEMORY_MAX_ITEMS', 2):
        for text in ('Important one', 'Important two'):
            memory.pin(memory.add(text).id)
        before = memory.items
        with pytest.raises(ValueError, match='pinned'):
            memory.add('New item')
        assert memory.items == before


@pytest.fixture
def reflection(hass, memory):
    options = {}
    rt = SimpleNamespace(options=options, speech=SimpleNamespace(guest_mode=False, _privacy_generation=0, _guest_generation=0),
        memory=memory, watches=SimpleNamespace(spend=Mock(return_value=True)),
        curiosity=SimpleNamespace(wonder=Mock(return_value=True)), followyou=SimpleNamespace(keep=Mock()), timeline=Mock())
    obj = Reflection(hass, 'learning', options, lambda: rt)
    obj._material = Mock(return_value=(['A recorded household event'], []))
    obj._store.async_delay_save = Mock()
    return obj, rt


LEARNED = {'memories': ['The bins go out on Thursday'], 'thoughts': ['The gate needed attention yesterday.'],
           'question': 'Is the kitchen fan meant to run overnight?', 'lessons': ['The big light means the ceiling light.']}


@pytest.mark.parametrize('change', ['forgotten', 'settings', 'disabled', 'guest', 'guest_cycle', 'stopped'])
async def test_late_reflection_cannot_restore_invalidated_learning(reflection, change):
    obj, rt = reflection
    async def generate(*args):
        if change == 'forgotten': rt.speech._privacy_generation += 1
        if change == 'settings': rt.options = dict(rt.options)
        if change == 'disabled': rt.options['reflection'] = False
        if change == 'guest': rt.speech.guest_mode = True
        if change == 'guest_cycle': rt.speech._guest_generation += 1
        if change == 'stopped': obj.async_stop()
        return {'data': LEARNED}
    with patch('custom_components.sentient.reflection.async_generate', side_effect=generate):
        result = await obj.async_reflect()
    assert 'skipped' in result and not rt.memory.items
    assert obj.last is None and not obj._busy
    rt.curiosity.wonder.assert_not_called()
    rt.followyou.keep.assert_not_called()
    rt.timeline.add.assert_not_called()


@pytest.mark.parametrize('blocked', ['off', 'guest', 'stopped'])
async def test_disabled_reflection_does_no_ai_work(reflection, blocked):
    obj, rt = reflection
    if blocked == 'off': rt.options['reflection'] = False
    if blocked == 'guest': rt.speech.guest_mode = True
    if blocked == 'stopped': obj.async_stop()
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock()) as ai:
        assert 'skipped' in await obj.async_reflect()
    ai.assert_not_awaited()
    rt.watches.spend.assert_not_called()


async def test_concurrent_and_repeated_reflection_use_one_call(reflection):
    obj, rt = reflection
    entered, release = asyncio.Event(), asyncio.Event()
    async def generate(*args):
        entered.set()
        await release.wait()
        return {'data': LEARNED}
    with patch('custom_components.sentient.reflection.async_generate', side_effect=generate) as ai:
        task = asyncio.create_task(obj.async_reflect())
        await entered.wait()
        assert 'skipped' in await obj.async_reflect()
        release.set()
        await task
        result = await obj.async_reflect()
    ai.assert_awaited_once()
    rt.watches.spend.assert_called_once()
    assert result['note'] == 'already reflected on this material today'
    result['thoughts'].clear()
    assert obj.last['thoughts']  # callers cannot mutate the stored cached result


async def test_new_material_can_be_reflected_on_without_repeating_old_work(reflection):
    obj, _ = reflection
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(return_value={'data': {}})) as ai:
        await obj.async_reflect()
        obj._material.return_value = (['A new recorded event'], [])
        await obj.async_reflect()
    assert ai.await_count == 2


async def test_failed_or_budget_blocked_reflection_can_recover(reflection):
    obj, rt = reflection
    rt.watches.spend.return_value = False
    assert 'skipped' in await obj.async_reflect()
    assert obj.last is None
    rt.watches.spend.return_value = True
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(side_effect=RuntimeError('offline'))):
        with pytest.raises(RuntimeError): await obj.async_reflect()
    assert not obj._busy
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(return_value={'data': LEARNED})):
        assert (await obj.async_reflect())['memories']


@pytest.mark.parametrize('out', [None, [], 1, 'unstructured'])
async def test_bad_reflection_envelope_saves_nothing(reflection, out):
    obj, rt = reflection
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(return_value={'data': out})):
        assert 'error' in await obj.async_reflect()
    assert not rt.memory.items and obj.last is None


async def test_reflection_rejects_nontext_items_and_private_lessons(reflection):
    obj, rt = reflection
    payload = {'memories': [23, None, {'text': 'invented'}, 'The bins go out Thursday'],
        'lessons': ['The bank account number is 1234', 7, 'The big light means the ceiling light'],
        'thoughts': [False, {}, 'One useful thought'], 'question': {'text': 'Do this'}}
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(return_value={'data': payload})):
        result = await obj.async_reflect()
    assert result['memories'] == ['The bins go out Thursday']
    assert result['lessons'] == ['The big light means the ceiling light']
    assert result['thoughts'] == ['One useful thought'] and result['question'] is None
    assert len(rt.memory.items) == 2


@pytest.fixture
def tidy(hass, memory):
    for n in range(9): memory.add(f'Distinct fact number {n} about topic{n}')
    rt = SimpleNamespace(memory=memory, options={}, speech=SimpleNamespace(guest_mode=False, _privacy_generation=0, _guest_generation=0), timeline=Mock())
    return Upkeep(hass, lambda: rt), rt


@pytest.mark.parametrize('change', ['edit', 'pin', 'add', 'delete', 'forget', 'settings', 'guest', 'guest_cycle', 'stopped'])
async def test_tidy_will_not_apply_a_stale_memory_snapshot(tidy, change):
    obj, rt = tidy
    ids = [m.id for m in rt.memory.items]
    after_change = []
    async def answer(*args):
        if change == 'edit': rt.memory.update(ids[0], 'An explicitly corrected fact')
        if change == 'pin': rt.memory.pin(ids[0])
        if change == 'add': rt.memory.add('A new fact from the person')
        if change == 'delete': rt.memory.forget(ids[0])
        if change == 'forget': rt.speech._privacy_generation += 1
        if change == 'settings': rt.options = dict(rt.options)
        if change == 'guest': rt.speech.guest_mode = True
        if change == 'guest_cycle': rt.speech._guest_generation += 1
        if change == 'stopped': obj.async_stop()
        after_change.extend(m.as_dict() for m in rt.memory.items)
        return {'remove': ids[0], 'merge': f'{ids[1]},{ids[2]} => Old model rewrite'}
    with patch.object(obj, '_ai', side_effect=answer):
        assert 'skipped' in await obj.async_tidy()
    assert [m.as_dict() for m in rt.memory.items] == after_change
    assert not obj._tidying
    rt.timeline.add.assert_not_called()


async def test_tidy_does_not_rewrite_pinned_fact_or_merge_an_id_with_itself(tidy):
    obj, rt = tidy
    first, second = rt.memory.items[:2]
    rt.memory.pin(first.id)
    with patch.object(obj, '_ai', new=AsyncMock(return_value={'merge': f'{first.id},{second.id} => Changed pinned content\n{second.id},{second.id} => Self rewrite'})):
        await obj.async_tidy()
    assert rt.memory.get(first.id) is first and first.text.startswith('Distinct fact')
    assert rt.memory.get(second.id) is second and second.text.startswith('Distinct fact')
    assert len(rt.memory.items) == 9


async def test_concurrent_tidy_does_not_double_spend(tidy):
    obj, _ = tidy
    entered, release = asyncio.Event(), asyncio.Event()
    async def answer(*args):
        entered.set()
        await release.wait()
        return {}
    with patch.object(obj, '_ai', side_effect=answer) as ai:
        task = asyncio.create_task(obj.async_tidy())
        await entered.wait()
        assert 'skipped' in await obj.async_tidy()
        release.set()
        await task
    ai.assert_awaited_once()


async def test_forgetting_clears_recent_derived_and_queued_material(hass):
    from tests.test_mind import _setup
    from custom_components.sentient.privacy import forget_recent
    entry = await _setup(hass)
    rt = entry.runtime_data
    old = (dt_util.utcnow() - timedelta(days=4)).isoformat()
    now = dt_util.utcnow().isoformat()
    rt.reflection._keep({'time': now, 'thoughts': ['A private thought'], 'lessons': ['A correction']})
    rt.curiosity.wonder('What is the kitchen fan for?', source='reflection')
    rt.curiosity.queue.append({'key': 'old', 'question': 'An older question', 'added': old})
    rt.curiosity.asked['recent'] = {'question': 'A recent question', 'time': now}
    rt.followyou.keep('A queued observation', source='thought')
    rt.followyou.queue.append({'id': 'old', 'text': 'Older message', 'kept': old})
    gone = forget_recent(rt)
    assert gone['reflection'] == 1 and rt.reflection.last is None
    assert rt.curiosity.listing() == {'wondering': ['An older question'], 'asked': []}
    assert [q['text'] for q in rt.followyou.queue] == ['Older message']
    assert gone['curiosity_records'] == 2 and gone['queued_messages'] == 1


@pytest.mark.parametrize('change', ['forget', 'dismiss', 'settings', 'stopped'])
async def test_late_curiosity_delivery_does_not_restore_forgotten_question(hass, change):
    options = {}
    speech = SimpleNamespace(_privacy_generation=0, _guest_generation=0)
    rt = SimpleNamespace(speech=speech, timeline=Mock())
    obj = Curiosity(hass, 'learning', options, lambda: rt)
    obj._save = Mock()
    obj.wonder('What is the kitchen fan for?')
    async def ask(*args, **kwargs):
        if change == 'forget':
            obj.queue.clear()
            speech._privacy_generation += 1
        if change == 'dismiss': obj.dismiss('kitchen')
        if change == 'settings': obj.options = dict(options)
        if change == 'stopped': obj.async_stop()
        return SimpleNamespace(spoken=True)
    speech.async_ask = ask
    result = await obj.async_ask(force=True)
    assert result['asked'] is None and not obj.asked and not obj._busy
    rt.timeline.add.assert_not_called()


async def test_tidy_cannot_delete_the_combined_fact_in_the_same_result(tidy):
    obj, rt = tidy
    first, second = rt.memory.items[:2]
    with patch.object(obj, '_ai', new=AsyncMock(return_value={'merge': f'{first.id},{second.id} => Combined fact', 'remove': first.id})):
        await obj.async_tidy()
    assert rt.memory.get(first.id).text == 'Combined fact'
    assert rt.memory.get(second.id) is None


async def test_tidy_keeps_correction_category_separate_from_general_facts(tidy):
    obj, rt = tidy
    first = rt.memory.items[0]
    lesson = rt.memory.add('A learned correction', 'correction')
    with patch.object(obj, '_ai', new=AsyncMock(return_value={'merge': f'{first.id},{lesson.id} => Mixed lesson'})):
        await obj.async_tidy()
    assert rt.memory.get(lesson.id).category == 'correction'
    assert first.text.startswith('Distinct fact')


async def test_reflection_rechecks_material_when_important_settings_change(reflection):
    obj, rt = reflection
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(return_value={'data': {}})) as ai:
        await obj.async_reflect()
        rt.options['house_rules'] = 'A newly configured house rule'
        await obj.async_reflect()
    assert ai.await_count == 2


async def test_reflection_does_not_report_known_items_as_new_learning(reflection):
    obj, rt = reflection
    rt.memory.add(LEARNED['memories'][0], source='service')
    rt.memory.add(LEARNED['lessons'][0], 'correction', source='service')
    with patch('custom_components.sentient.reflection.async_generate', new=AsyncMock(return_value={'data': LEARNED})):
        result = await obj.async_reflect()
    assert not result['memories'] and not result['lessons']
    assert len(rt.memory.items) == 2


def test_reflection_material_includes_latest_correction(hass):
    now = dt_util.utcnow().isoformat()
    rt = SimpleNamespace(timeline=SimpleNamespace(since=lambda *args: []),
        conversations=SimpleNamespace(recent=lambda **kwargs: [{'start': now, 'turns': [
            {'who': 'them', 'text': f'Earlier turn {i}'} for i in range(10)] + [
            {'who': 'them', 'text': 'No, I meant the bedroom lamp.'}, {'who': 'me', 'text': 'Understood.'}]}]))
    obj = Reflection(hass, 'last-turns', {}, lambda: rt)
    _, talk = obj._material(rt)
    assert 'No, I meant the bedroom lamp.' in talk[0]
    assert 'Earlier turn 0' not in talk[0]
