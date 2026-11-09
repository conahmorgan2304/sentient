"""Diagnostics redaction, face names, page access and unlocking by voice."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.sentient.const import (
    CONF_AWAY_PEOPLE,
    CONF_FACE_SENSOR,
    CONF_FACES,
    CONF_NOTIFY_TARGETS,
    CONF_PAGE_ADMIN_ONLY,
    CONF_PHONE_APPS,
    CONF_VOICE_UNLOCK,
    CONF_WELCOME_PEOPLE,
)
from custom_components.sentient.diagnostics import TO_REDACT
from custom_components.sentient.websocket import page_access


def test_personal_settings_are_redacted():
    for key in (CONF_NOTIFY_TARGETS, CONF_PHONE_APPS, CONF_WELCOME_PEOPLE, CONF_AWAY_PEOPLE):
        assert key in TO_REDACT


def _connection(admin: bool):
    conn = MagicMock()
    conn.user = SimpleNamespace(is_admin=admin)
    return conn


def _run(hass, options, admin):
    from custom_components.sentient import websocket

    calls = []

    @page_access
    def handler(hass, connection, msg):
        calls.append(msg["id"])

    entry = SimpleNamespace(data={}, options=options)
    original = websocket._entry
    websocket._entry = lambda _hass: entry
    try:
        conn = _connection(admin)
        handler(hass, conn, {"id": 1})
    finally:
        websocket._entry = original
    return calls, conn


async def test_page_open_to_everyone_by_default(hass):
    calls, conn = _run(hass, {}, admin=False)
    assert calls == [1] and not conn.send_error.called


async def test_admin_only_refuses_family(hass):
    calls, conn = _run(hass, {CONF_PAGE_ADMIN_ONLY: True}, admin=False)
    assert calls == [] and conn.send_error.called


async def test_admin_only_still_lets_admins_in(hass):
    calls, _ = _run(hass, {CONF_PAGE_ADMIN_ONLY: True}, admin=True)
    assert calls == [1]


async def test_face_names_need_faces_switched_on(hass):
    from custom_components.sentient.visitors import Visitors

    hass.states.async_set("sensor.face", "alice")
    for faces, expected in ((False, None), (True, "Alice")):
        v = Visitors.__new__(Visitors)
        v.hass = hass
        v.options = {CONF_FACE_SENSOR: "sensor.face", CONF_FACES: faces}
        assert v.recognised_name() == expected


async def test_unlock_needs_its_own_switch(hass):
    from unittest.mock import Mock, patch

    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.sentient.control import Control

    async_mock_service(hass, "lock", "unlock")
    hass.states.async_set("lock.door", "locked")
    action = [{"entity_id": "lock.door", "action": "unlock"}]
    with pytest.raises(ValueError, match="switched off"):
        await Control(hass, {}, Mock()).validate(action, None)
    with patch("custom_components.sentient.control.async_should_expose", return_value=True):
        await Control(hass, {CONF_VOICE_UNLOCK: True}, Mock()).validate(action, None)  # allowed: no error


async def test_locking_never_needs_the_unlock_switch(hass):
    from unittest.mock import Mock, patch

    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.sentient.control import Control

    async_mock_service(hass, "lock", "lock")
    hass.states.async_set("lock.door", "unlocked")
    with patch("custom_components.sentient.control.async_should_expose", return_value=True):
        await Control(hass, {}, Mock()).validate([{"entity_id": "lock.door", "action": "lock"}], None)
