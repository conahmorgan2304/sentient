"""Energy report from Energy-dashboard devices."""

from datetime import datetime, timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.sentient import energy


async def test_report_flags_unusual(hass: HomeAssistant, freezer) -> None:
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 22, 18, 30, tzinfo=tz))
    midnight = datetime(2026, 10, 22, 0, 0, tzinfo=tz)

    def rows(change_today, change_past):
        out = []
        for d in range(0, 8):
            day = midnight - timedelta(days=d)
            for h in range(0, 24):
                t = day + timedelta(hours=h)
                if t >= datetime(2026, 10, 22, 18, 0, tzinfo=tz):
                    continue
                out.append({"start": dt_util.as_utc(t).timestamp(), "change": change_today if d == 0 else change_past})
        return out

    data = {"sensor.heater": rows(0.3, 0.05), "sensor.fridge": rows(0.04, 0.04)}

    class Inst:
        async def async_add_executor_job(self, fn, *args):
            return data

    with patch.object(energy, "_devices", return_value={"sensor.heater": "Heater", "sensor.fridge": "Fridge"}), \
         patch("homeassistant.components.recorder.get_instance", return_value=Inst()):
        report = await energy.async_report(hass)
    heater = report["devices"][0]
    assert heater["name"] == "Heater" and heater["kwh"] == 5.4 and heater["usual"] == 0.9
    assert [u["name"] for u in report["unusual"]] == ["Heater"]
    assert energy.describe(report) == ["Heater has used 5.4 kWh today, about 6.0× its usual"]
