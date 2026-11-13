"""Ensure root-cause reporting is conservative and private."""
from types import SimpleNamespace

from custom_components.sentient.dependency_health import evaluate, runtime_dependencies


def test_degraded_and_unknown_are_not_confused_with_working():
    result = evaluate({"camera": False, "mqtt": None, "tts": True}, {
        "alert": ("camera", "tts"),
        "feed": ("mqtt", "tts"),
        "speech": ("tts",),
    })
    assert result["features"]["alert"]["status"] == "degraded"
    assert result["features"]["alert"]["failed_dependencies"] == ["camera"]
    assert result["features"]["feed"]["status"] == "unknown"
    assert result["features"]["speech"]["status"] == "available"
    assert result["degraded_count"] == 1
    assert result["unknown_count"] == 1


def test_no_household_names_in_public_health_output():
    result = evaluate({"camera.kitchen": False}, {"camera_investigation": ("camera.kitchen",)})
    assert result["features"]["camera_investigation"]["status"] == "degraded"
    # Individual dependency names must be fixed category labels at invocation.
    # Runtime data is deliberately built from an allowlist.
    rt = SimpleNamespace(security_events=SimpleNamespace(_stopped=False))
    hass = SimpleNamespace(config=SimpleNamespace(components={"recorder"}),
                           states=SimpleNamespace(async_all=lambda domain: []))
    deps = runtime_dependencies(rt, hass)
    assert set(deps) == {"voice_pipeline", "tts", "speaker_recognition", "frigate",
                         "camera_evidence", "recorder", "climate"}
    assert deps["voice_pipeline"] is None
    assert deps["camera_evidence"] is True
    assert deps["recorder"] is True


def test_no_false_claim_of_fridget_or_climate_availability():
    rt = SimpleNamespace()
    hass = SimpleNamespace(config=SimpleNamespace(components=set()),
                           states=SimpleNamespace(async_all=lambda domain: []))
    result = evaluate(runtime_dependencies(rt, hass))
    assert result["features"]["learned_heating"]["status"] == "degraded"
    assert result["features"]["voice_conversation"]["status"] == "unknown"
