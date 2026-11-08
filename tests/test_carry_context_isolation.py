"""Automatic wake-word history is scoped and cannot restore invalidated conversations."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import Context

from custom_components.sentient.switch import set_guest_mode
from tests.test_voice_sessions_control import agent, request, response


def setup(hass):
    obj, rt = agent(hass, {'carry_on_minutes': 3})
    rt.speech = SimpleNamespace(guest_mode=False, _privacy_generation=0)
    return obj, rt


async def talk(obj, inputs):
    seen = []
    async def converse(user_input):
        seen.append(user_input.conversation_id)
        result = response('Here is the reply.')
        result.conversation_id = user_input.conversation_id
        return result
    with patch.object(obj, '_process_turn', side_effect=converse):
        for item in inputs:
            await obj.async_process(item)
    return seen


@pytest.mark.parametrize('different', ['user', 'device', 'satellite', 'anonymous'])
async def test_same_speaker_different_owner_cannot_reuse_history(hass, different):
    obj, _ = setup(hass)
    first = replace(request('Private question', cid='private', user='alice'), satellite_id='satellite.shared')
    second = replace(first, conversation_id='fresh', text='What did I ask?')
    if different == 'user': second = replace(second, context=Context(user_id='bob'))
    if different == 'device': second = replace(second, device_id='other-device')
    if different == 'satellite': second = replace(second, satellite_id='satellite.other')
    if different == 'anonymous': second = replace(second, context=Context())
    assert await talk(obj, [first, second]) == ['private', 'fresh']


async def test_exact_owner_keeps_history_and_separate_user_does_not_replace_it(hass):
    obj, _ = setup(hass)
    assert await talk(obj, [request('first', cid='alice-id', user='alice'),
        request('second', cid='bob-id', user='bob'), request('follow up', cid='new', user='alice')]) == ['alice-id', 'bob-id', 'alice-id']


@pytest.mark.parametrize('change', ['forgotten', 'settings', 'guest_cycle', 'disabled'])
async def test_invalidated_history_is_not_automatically_resumed(hass, change):
    obj, rt = setup(hass)
    await talk(obj, [request('first', cid='old')])
    if change == 'forgotten': rt.speech._privacy_generation += 1
    if change == 'settings': rt.options = dict(rt.options)
    if change == 'guest_cycle':
        set_guest_mode(hass, obj._entry, True)
        set_guest_mode(hass, obj._entry, False)
    if change == 'disabled': rt.options['carry_on_minutes'] = 0
    assert await talk(obj, [request('follow up', cid='fresh')]) == ['fresh']


async def test_guest_never_resumes_or_saves_automatic_history(hass):
    obj, rt = setup(hass)
    await talk(obj, [request('private', cid='private')])
    set_guest_mode(hass, obj._entry, True)
    assert await talk(obj, [request('guest one', cid='guest-one'), request('guest two', cid='guest-two')]) == ['guest-one', 'guest-two']
    assert not obj._carry
    set_guest_mode(hass, obj._entry, False)
    assert await talk(obj, [request('after', cid='fresh')]) == ['fresh']


@pytest.mark.parametrize('change', ['forgotten', 'settings', 'guest_cycle'])
async def test_late_reply_cannot_seed_new_history_after_invalidation(hass, change):
    obj, rt = setup(hass)
    async def process(user_input):
        if change == 'forgotten': rt.speech._privacy_generation += 1
        if change == 'settings': rt.options = dict(rt.options)
        if change == 'guest_cycle':
            set_guest_mode(hass, obj._entry, True)
            set_guest_mode(hass, obj._entry, False)
        result = response('Delayed reply.')
        result.conversation_id = 'old'
        return result
    with patch.object(obj, '_process_turn', side_effect=process):
        await obj.async_process(request('first', cid='old'))
    assert not obj._carry
    assert await talk(obj, [request('next', cid='fresh')]) == ['fresh']


async def test_carry_history_has_a_size_limit(hass):
    obj, _ = setup(hass)
    await talk(obj, [request('hello', cid=f'cid-{i}', device=f'device-{i}') for i in range(105)])
    assert len(obj._carry) == 100
