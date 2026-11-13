"""Run synthetic/redacted security scenarios without Home Assistant actions.

Usage (in the Sentient test environment):
  python -m custom_components.sentient.cognitive_replay_cli tests/fixtures/cognitive_scenarios.json

Input JSON is a list of {"incident": {...}, "expected_stage": "..."}.
Output contains aggregate accuracy only, never incident text or identifiers.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

from .cognitive_replay import evaluate_scenarios

MAX_BYTES = 1024 * 1024


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentient read-only cognitive incident replay")
    parser.add_argument("scenarios", type=Path, help="local JSON containing synthetic or explicitly redacted examples")
    parser.add_argument("--at", help="evaluation time in ISO-8601 format; defaults to current UTC")
    args = parser.parse_args(argv)
    try:
        if args.scenarios.stat().st_size > MAX_BYTES:
            raise ValueError("Scenario file exceeds 1 MiB")
        cases = json.loads(args.scenarios.read_text(encoding="utf-8"))
        if not isinstance(cases, list) or len(cases) > 500:
            raise ValueError("Expected a JSON list of at most 500 scenarios")
        now = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
        result = evaluate_scenarios(cases, now=now)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as err:
        # Do not echo private JSON values or filenames from malformed inputs.
        parser.exit(2, f"Replay input invalid: {type(err).__name__}\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
