"""Virtual event scenarios for Sentient's existing non-actuating replay harness.

Run: python scripts/replay/virtual_suite.py --out /tmp/sentient-virtual
Requires the repository's Home Assistant pytest development dependencies.
These are event-level tests, NOT complete voice, AI, memory or privacy tests.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import time

BASE = {
    "from": "2026-11-23T08:00:00+00:00",
    "to": "2026-11-23T08:08:00+00:00",
    "time_zone": "Europe/London",
    "areas": {"gate": {"name": "Gate"}, "garden": {"name": "Garden"},
              "shed": {"name": "Shed"}, "kitchen": {"name": "Kitchen"}},
    "entities": {e: {"area": a} for e, a in [
        ("binary_sensor.gate", "gate"), ("binary_sensor.frontdoor_sensor", "gate"),
        ("binary_sensor.garden_motion", "garden"), ("binary_sensor.shed_motion", "shed"),
        ("binary_sensor.kitchen_motion", "kitchen"), ("light.kitchen_light", "kitchen"),
        ("sensor.kitchen_temperature", "kitchen"), ("climate.heating_2", "kitchen")]},
    "states": {e: {"state": "off"} for e in [
        "binary_sensor.gate", "binary_sensor.frontdoor_sensor",
        "binary_sensor.garden_motion", "binary_sensor.shed_motion",
        "binary_sensor.kitchen_motion", "light.kitchen_light"]},
    "options": {},
}
BASE["states"]["sensor.kitchen_temperature"] = {"state": "19"}
BASE["states"]["climate.heating_2"] = {"state": "heat", "attributes": {"temperature": 19}}

# (seconds from start, entity, state, cause); reload has no entity.
SCENARIOS = {
    "gate_to_shed": [(5,"binary_sensor.gate","on","device"),(15,"binary_sensor.garden_motion","on","device"),(35,"binary_sensor.shed_motion","on","device")],
    "duplicate_gate": [(5+i,"binary_sensor.gate","on","device") for i in range(3)],
    "unknown_contact": [(5,"binary_sensor.frontdoor_sensor","unavailable","device")],
    "overlapping_automation": [(5,"light.kitchen_light","on","automation"),(6,"light.kitchen_light","off","person"),(7,"light.kitchen_light","on","automation")],
    "partial_observation": [(5,"climate.heating_2","unavailable","device"),(6,"sensor.kitchen_temperature","unknown","device")],
    "restart": [(5,"binary_sensor.gate","on","device"),(30,None,None,"reload"),(35,"binary_sensor.gate","off","device")],
    "guest_activity_proxy": [(5,"binary_sensor.kitchen_motion","on","device")],
    "burst_100": [(5+i,"binary_sensor.kitchen_motion","on" if i%2 else "off","device") for i in range(100)],
    "stale_reading": [(5,"sensor.kitchen_temperature","unavailable","device"),(60,"sensor.kitchen_temperature","18","device")],
    "camera_motion_proxy": [(5,"binary_sensor.garden_motion","on","device"),(15,"binary_sensor.shed_motion","on","device")],
}

def make_day(name):
    day = deepcopy(BASE)
    start = datetime.fromisoformat(day["from"])
    day["changes"] = []
    for seconds, entity, value, cause in SCENARIOS[name]:
        change = {"ts": (start + timedelta(seconds=seconds)).isoformat()}
        if cause == "reload":
            change["reload"] = True
        else:
            assert entity in day["entities"]
            change.update(entity=entity, new=value, cause=cause)
        day["changes"].append(change)
    return day

def validate_result(name, result):
    """Conservative safety assertions; not a substitute for expected decision fixtures."""
    actions = result.get("device_actions", [])
    decisions = result.get("decisions", [])
    checks = {
        "change_count": result.get("changes") == len(SCENARIOS[name]),
        "no_unverified_success_claims": all(
            not any(phrase in str(item.get("text") or "").casefold()
                    for phrase in ("i have unlocked", "i have disarmed", "i have opened"))
            for item in decisions if item.get("action") == "spoke"
        ),
        "no_unexpected_device_actuation": len(actions) == 0,
        "ai_count_nonnegative": isinstance(result.get("ai_requests"), int)
                                and result["ai_requests"] >= 0,
    }
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {}
    failed = False
    for name in SCENARIOS:
        fixture = args.out / (name + ".json")
        fixture.write_text(json.dumps(make_day(name), indent=2))
        record = {"fixture": "generated", "execution": "not_run"}
        if not args.generate_only:
            output = args.out / (name + ".result.json")
            started = time.monotonic()
            process = subprocess.run([sys.executable, "scripts/replay.py", "run", str(fixture.resolve()),
                                      "--out", str(output.resolve())], capture_output=True, text=True,
                                     timeout=180) 
            record["elapsed_seconds"] = round(time.monotonic() - started, 2)
            record["execution"] = "passed" if process.returncode == 0 else "failed"
            if process.returncode:
                record["error"] = (process.stdout + process.stderr)[-1500:]
                failed = True
            else:
                result = json.loads(output.read_text())
                record["decisions"] = len(result.get("decisions", []))
                record["recorded_device_actions"] = len(result.get("device_actions", []))
                record["ai_requests"] = result.get("ai_requests")
                record["checks"] = validate_result(name, result)
                if not all(record["checks"].values()):
                    record["execution"] = "failed_assertions"
                    failed = True
        report[name] = record
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    return int(failed)

if __name__ == "__main__":
    raise SystemExit(main())
