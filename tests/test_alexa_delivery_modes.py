"""Keep announcement chimes configurable while supporting single-Echo targets."""
from unittest.mock import AsyncMock, patch
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service
from custom_components.sentient.speech import SpeechRouter


@pytest.mark.parametrize('force,chime,kind', [(False, True, 'announce'), (True, True, 'tts'), (False, False, 'tts')])
async def test_alexa_mode_preserves_per_speaker_receipts(hass, force, chime, kind):
    router = SpeechRouter(hass, {'alexa_tts_only': force, 'chime': chime})
    calls = async_mock_service(hass, 'notify', 'alexa_media')
    with patch('custom_components.sentient.speech._platform', return_value='alexa_media'), patch('custom_components.sentient.speech.asyncio.sleep', new_callable=AsyncMock):
        results = await router._speak('Hello', ['media_player.one', 'media_player.two'])
    assert [c.data['target'] for c in calls] == ['media_player.one', 'media_player.two']
    assert all(c.data['data']['type'] == kind for c in calls)
    assert [r['status'] for r in results] == ['accepted', 'accepted']
