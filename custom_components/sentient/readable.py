"""A readable copy of what Sentient knows and does: memories, routines, lists and settings as plain text.

For reading, printing or keeping — the JSON backup is still the file to restore from. Secrets and addresses on
the house's network (tokens, URLs) are left out.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .kinds import KINDS, summary

SECRET = ("token", "url", "password", "key")


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "—"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items()) or "—"
    return "—" if v in (None, "") else str(v)


async def async_labels(hass: HomeAssistant) -> dict[str, str]:
    """Each setting's label as the settings page shows it."""
    from homeassistant.helpers.translation import async_get_translations

    from .const import DOMAIN

    labels: dict[str, str] = {}
    for category in ("options", "config"):
        try:
            found = await async_get_translations(hass, hass.config.language or "en", category, [DOMAIN])
        except Exception:  # noqa: BLE001 - raw names will do
            continue
        for path, text in found.items():
            if ".data." in path:
                labels.setdefault(path.rsplit(".", 1)[-1], text)
    return labels


def build(hass: HomeAssistant, rt: Any, title: str, labels: dict[str, str] | None = None) -> str:
    now = dt_util.now()
    out = [f"# {title} — what it knows and does", f"Made {now:%A %-d %B %Y at %H:%M}.", ""]

    memories = sorted(rt.memory.items, key=lambda m: (not m.pinned, m.category, m.created))
    out += [f"## Memories ({len(memories)})", ""]
    for m in memories:
        out.append(f"- {m.text}" + (" *(pinned)*" if m.pinned else "") + f" — {m.category}")
    out.append("")

    routines = rt.learner.all()
    out += [f"## Learned routines ({len(routines)})", ""]
    for r in routines:
        state = "the house does it" if r.adopted else "declined" if r.declined else "noticed"
        out.append(f"- {r.describe()} ({state})")
    out.append("")

    for kind, spec in KINDS.items():
        if "view_of" in spec:
            store = rt.lists.get(spec["view_of"])
        else:
            store = rt.lists.get(kind)
        if store is None:
            continue
        from .kinds import belongs

        items = [i for i in store.listing() if belongs(kind, i)]
        if not items:
            continue
        out += [f"## {spec['title']} ({len(items)})", ""]
        for item in items:
            line = summary(kind, item, hass)
            out.append(f"- **{item['name']}**" + (f" — {line}" if line else "") + ("" if item.get("enabled", True) else " *(off)*"))
        out.append("")

    watches = rt.watches.watches
    if watches:
        out += [f"## Watches and reminders ({len(watches)})", ""]
        out += [f"- {w.what} ({'reminder at' if w.kind == 'reminder' else 'until'} {w.summary()['until']})" for w in watches]
        out.append("")

    opts = {**rt.options}
    out += ["## Settings", ""]
    for key in sorted(opts):
        if any(s in key for s in SECRET):
            continue
        out.append(f"- {(labels or {}).get(key) or key.replace('_', ' ')}: {_value(opts[key])}")
    out.append("")
    return "\n".join(out)
