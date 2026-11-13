#!/usr/bin/env python3
"""Replay benchmark: run recorded days of a real house through two versions of Sentient and compare them.

A day comes from Settings → Setup doctor → DOWNLOAD THE LAST DAY (or the regression days in
tests/fixtures/replay/). Each version gets the same changes, at the same simulated times, with nothing spoken,
no device touched and no AI (see scripts/replay/runner.py). What they decided — including staying silent — is
compared.

  python scripts/replay.py bench DAY.json [DAY2.json ...] --old v1.5.11 [--new HEAD|WORKTREE] [--report out.md]
  python scripts/replay.py run DAY.json --out result.json [--ref v1.5.11]
  python scripts/replay.py compare OLD.json NEW.json [--live DAY.json]

`bench` exits 1 when the new version is worse on a measure that can only be bad: repeats, claims that it did
something, unhandled errors, or noticeably more AI requests. Changed announcements are listed for a person to
judge — whether a new announcement is right needs the household's verdicts, which this can't supply.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "replay"
REPEAT_SECONDS = 3 * 3600
MATCH_SECONDS = 15 * 60
AI_SLACK = 1.2  # more than 20% more AI requests counts as worse
CLAIM = re.compile(r"\bI(?:'ve| have)? (?:turned|switched|put|set|opened|closed|locked|unlocked|started|stopped)\b", re.I)


# ----- running ---------------------------------------------------------------------------------------------------
def run(day: Path, out: Path, ref: str | None = None) -> dict:
    """Replay one day against the working tree (ref None or WORKTREE) or a git ref checked out on the side."""
    tmp = None
    source = REPO
    if ref and ref != "WORKTREE":
        tmp = Path(tempfile.mkdtemp(prefix="sentient-replay-"))
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(tmp), ref], check=True,
                       capture_output=True)
        source = tmp
    try:
        env = {**os.environ, "REPLAY_DAY": str(day.resolve()), "REPLAY_OUT": str(out.resolve())}
        out.unlink(missing_ok=True)
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "-c", str(HARNESS / "pytest.ini"), "--rootdir", str(HARNESS),
                               str(HARNESS / "test_day.py")], cwd=source, env=env, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            raise SystemExit(f"Replay of {day.name} on {ref or 'the working tree'} failed:\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}")
        result = json.loads(out.read_text(encoding="utf-8"))
        if not Path(result["code"]).resolve().is_relative_to(source.resolve()):
            raise SystemExit(f"The replay loaded {result['code']}, not the code in {source}")
        result["ref"] = ref or "WORKTREE"
        out.write_text(json.dumps(result, indent=1), encoding="utf-8")
        return result
    finally:
        if tmp is not None:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(tmp)], capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)


# ----- measuring -------------------------------------------------------------------------------------------------
def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\d+", "#", (text or "").casefold())).strip()


def measures(result: dict) -> dict:
    d = result["decisions"]
    spoke = [x for x in d if x["action"] == "spoke"]
    repeats = 0
    last: dict[str, float] = {}
    for x in spoke:
        # the same words again (numbers included: "open for 15 minutes" then "45 minutes" is a reminder)
        key, at = (x["text"] or "").casefold().strip(), _ts(x["time"])
        if key in last and at - last[key] < REPEAT_SECONDS:
            repeats += 1
        last[key] = at
    return {
        "spoken": len(spoke),
        "sent to phones": sum(1 for x in d if x["phoned"]),
        "held back": len([x for x in d if x["action"] == "held"]),
        "only noted": len([x for x in d if x["action"] == "noted"]),
        "repeats within 3 h": repeats,
        "claims it did something": sum(1 for x in spoke if CLAIM.search(x["text"] or "")),
        "AI requests": result.get("ai_requests", 0),
        "device actions": len(result.get("device_actions") or []),
        "held, by reason": dict(Counter(x["reason"] or "?" for x in d if x["action"] == "held")),
    }


def _match(a: list[dict], b: list[dict]) -> tuple[list[dict], list[dict]]:
    """Announcements in a but not b, and b but not a: same wording (numbers aside) within 15 minutes."""
    left, right = list(a), list(b)
    for x in a:
        for y in right:
            if _norm(x["text"]) == _norm(y["text"]) and abs(_ts(x["time"]) - _ts(y["time"])) <= MATCH_SECONDS:
                left.remove(x)
                right.remove(y)
                break
    return left, right


def compare(old: dict, new: dict, live: dict | None = None) -> tuple[str, list[str]]:
    mo, mn = measures(old), measures(new)
    worse = []
    for key in ("repeats within 3 h", "claims it did something"):
        if mn[key] > mo[key]:
            worse.append(f"{key}: {mo[key]} → {mn[key]}")
    if mn["AI requests"] > max(mo["AI requests"] * AI_SLACK, mo["AI requests"] + 2):
        worse.append(f"AI requests: {mo['AI requests']} → {mn['AI requests']}")
    lines = [f"### {old.get('from', '')[:16]} → {old.get('to', '')[:16]} · {old.get('changes')} recorded changes", "",
             f"| | {old.get('ref')} | {new.get('ref')} |" + (" live |" if live else ""),
             "| --- | ---: | ---: |" + (" ---: |" if live else "")]
    ml = measures({"decisions": live["decisions"], "ai_requests": "–"}) if live else None
    for key in mo:
        if key == "held, by reason":
            continue
        lines.append(f"| {key} | {mo[key]} | {mn[key]} |" + (f" {ml[key]} |" if ml else ""))
    reasons = sorted(set(mo["held, by reason"]) | set(mn["held, by reason"]))
    for r in reasons:
        lines.append(f"| held: {r} | {mo['held, by reason'].get(r, 0)} | {mn['held, by reason'].get(r, 0)} |" + (" |" if ml else ""))
    said = lambda r: [x for x in r["decisions"] if x["action"] == "spoke" or x["phoned"]]  # noqa: E731
    gone, added = _match(said(old), said(new))
    if gone or added:
        lines += ["", "**Announcements that changed** (for a person to judge):"]
        lines += [f"- only {old.get('ref')}: {x['time'][11:16]} [{x['source']}] {x['text']}" for x in gone]
        lines += [f"- only {new.get('ref')}: {x['time'][11:16]} [{x['source']}] {x['text']}" for x in added]
    else:
        lines += ["", "Same announcements."]
    if worse:
        lines += ["", "**Worse:** " + "; ".join(worse)]
    return "\n".join(lines), worse


# ----- command line ----------------------------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench")
    b.add_argument("days", nargs="+", type=Path)
    b.add_argument("--old", required=True)
    b.add_argument("--new", default="WORKTREE")
    b.add_argument("--report", type=Path)
    b.add_argument("--keep", action="store_true", help="keep each version's full result (decisions, speech, phones)")
    r = sub.add_parser("run")
    r.add_argument("day", type=Path)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--ref")
    c = sub.add_parser("compare")
    c.add_argument("old", type=Path)
    c.add_argument("new", type=Path)
    c.add_argument("--live", type=Path)
    args = p.parse_args()

    if args.cmd == "run":
        run(args.day, args.out, args.ref)
        return 0
    if args.cmd == "compare":
        live = json.loads(args.live.read_text()) if args.live else None
        text, worse = compare(json.loads(args.old.read_text()), json.loads(args.new.read_text()), live)
        print(text)
        return 1 if worse else 0
    work = Path(tempfile.mkdtemp(prefix="sentient-bench-"))
    sections, failed = [f"## Replay benchmark: {args.old} → {args.new}", ""], False
    for day in args.days:
        old = run(day, work / f"{day.stem}.old.json", args.old)
        new = run(day, work / f"{day.stem}.new.json", args.new)
        live = json.loads(day.read_text())
        text, worse = compare(old, new, live if live.get("decisions") else None)
        sections += [text, ""]
        failed |= bool(worse)
    report = "\n".join(sections)
    print(report)
    if args.keep:
        print(f"\nFull results: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
