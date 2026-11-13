"""Integration audit: is everything in the integration actually connected?

A module nothing in the running integration can reach, a setting nothing reads, or a panel command nothing
registers is a feature that only exists on paper. These checks fail on any new one. The KNOWN lists are the
gaps accepted today; they can only shrink — wiring something in (or removing it) means taking it off the list,
and the test says so when that's due.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

from custom_components.sentient.websocket import GROUPS

ROOT = Path(__file__).parents[1] / "custom_components" / "sentient"
FRONTEND = ROOT / "frontend"

# What Home Assistant loads by name: the integration, its config flow and platforms, diagnostics and repairs.
ENTRY_POINTS = {"__init__", "config_flow", "diagnostics", "system_health", "repairs"}

# Reachable only from the developer tool scripts/cognitive_replay.py (not at run time). Accepted: it's the
# offline replay of security scenarios, kept with the integration so it uses the same rules.
KNOWN_DEV_ONLY = {"cognitive_replay"}


def _modules() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in ROOT.glob("*.py")}


def _imports(source: str) -> set[str]:
    """Every sibling module a module imports, including imports inside functions."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                found.add(node.module.split(".")[0])
            else:
                found.update(alias.name for alias in node.names)
    return found


def _platforms(init: str) -> set[str]:
    match = re.search(r"PLATFORMS[^=]*=\s*\[([^\]]*)\]", init)
    names = re.findall(r"Platform\.(\w+)|[\"'](\w+)[\"']", match.group(1)) if match else []
    return {(a or b).lower() for a, b in names}


def test_every_module_is_reachable_from_what_home_assistant_loads() -> None:
    modules = _modules()
    graph = {name: _imports(src) & modules.keys() for name, src in modules.items()}
    start = (ENTRY_POINTS | _platforms(modules["__init__"])) & modules.keys()
    seen, todo = set(start), list(start)
    while todo:
        for nxt in graph[todo.pop()] - seen:
            seen.add(nxt)
            todo.append(nxt)
    unreachable = set(modules) - seen
    assert unreachable - KNOWN_DEV_ONLY == set(), (
        f"Nothing in the running integration reaches {sorted(unreachable - KNOWN_DEV_ONLY)}. "
        "Wire it in where it gets real inputs, or remove it (CLAUDE.md: a module nothing feeds isn't a feature).")
    assert KNOWN_DEV_ONLY <= unreachable, f"{sorted(KNOWN_DEV_ONLY - unreachable)} is reached now: take it off KNOWN_DEV_ONLY"


def _constants(modules: dict[str, str]) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for src in modules.values():
        for name, value in re.findall(r"""^\s*(\w+)\s*=\s*["']([a-z0-9_]+)["']""", src, re.M):
            names.setdefault(value, set()).add(name)
    return names


def test_every_setting_is_read_by_something() -> None:
    """A setting on the Settings page that no code reads does nothing when switched."""
    modules = _modules()
    names = _constants(modules)
    panel = "".join(p.read_text(encoding="utf-8") for p in FRONTEND.glob("*.js"))

    def read(key: str) -> bool:
        quoted = r"""["']%s["']""" % re.escape(key)
        for mod, src in modules.items():
            if mod in ("config_flow", "const"):
                continue  # where settings are declared, not used
            body = "\n".join(line for line in src.splitlines() if not re.match(r"""^\s*\w+\s*=\s*%s\s*(#.*)?$""" % quoted, line))
            if mod == "websocket":
                # the Settings page lists every key here; only an actual read counts
                if re.search(r"\.get\(\s*(%s)" % "|".join([quoted, *map(re.escape, names.get(key, ()))]), body):
                    return True
                continue
            if re.search(quoted, body) or any(re.search(r"\b%s\b" % re.escape(n), body) for n in names.get(key, ())):
                return True
        return bool(re.search(r"\b%s\b" % re.escape(key), panel))  # a setting the page itself reads

    unread = [key for group in GROUPS for key in group[4] if not read(key)]
    assert unread == [], f"Settings nothing reads: {unread}"


def test_every_command_the_page_sends_is_registered() -> None:
    """A button calling a command the integration doesn't have fails only when someone presses it."""
    sent = set()
    for js in FRONTEND.glob("*.js"):
        sent |= set(re.findall(r"""type:\s*["'](sentient/[a-z_/]+)["']""", js.read_text(encoding="utf-8")))
    registered = set()
    for src in _modules().values():
        registered |= set(re.findall(r"""["']type["']\)\s*:\s*["'](sentient/[a-z_/]+)["']""", src))
    assert sent - registered == set(), f"The page sends commands nothing registers: {sorted(sent - registered)}"
