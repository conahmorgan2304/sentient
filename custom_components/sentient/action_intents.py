"""Privacy-bounded, advisory cross-feature action-intent monitor.

Never authorises, dispatches, blocks, retries or undoes actions. It only
compares recently dispatched intents from Sentient's independent subsystems.
A conflict is a *candidate* for review, not proof of a broken automation.
"""
from __future__ import annotations

from collections import OrderedDict, Counter
from datetime import datetime, timedelta, timezone
from hashlib import blake2s
from secrets import token_bytes

WINDOW = timedelta(seconds=120)
MAX_TARGETS = 128
_SIDES = {"turn_on": "on", "on": "on", "turn_off": "off", "off": "off",
          "open": "open", "close": "close"}


class ActionIntentMonitor:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._secret = token_bytes(32)
        self._last: OrderedDict[str, tuple[datetime, str, str]] = OrderedDict()
        self._counts = Counter()
        self._recent: list[dict[str, str]] = []

    def observe(self, entity_id: str, action: str, source: str, *,
                when: datetime | None = None) -> dict[str, str] | None:
        """Note a dispatched intent; never assert device completion.

        Keep only keyed entity digests, coarse source categories and timestamps.
        Names, state values, room labels and requests are not stored.
        """
        if not isinstance(entity_id, str) or "." not in entity_id:
            return None
        side = _SIDES.get(action)
        if side is None or source not in {"guarded_voice", "motion_lights", "auto_off", "rules", "heating"}:
            return None
        now = when or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("ActionIntentMonitor needs a timezone-aware timestamp")
        now = now.astimezone(timezone.utc)
        digest = blake2s(entity_id.encode(), key=self._secret, digest_size=16).hexdigest()
        for key, (stamp, _, _) in list(self._last.items()):
            if now - stamp > WINDOW or stamp > now:
                self._last.pop(key, None)
        previous = self._last.get(digest)
        self._last[digest] = (now, side, source)
        self._last.move_to_end(digest)
        while len(self._last) > MAX_TARGETS:
            self._last.popitem(last=False)
        self._counts["intents"] += 1
        if previous is None:
            return None
        stamp, prev_side, prev_source = previous
        if not timedelta(0) <= now - stamp <= WINDOW or prev_source == source or prev_side == side:
            return None
        result = {"earlier_source": prev_source, "later_source": source,
                  "relationship": "opposing_intents", "verified_conflict": "no"}
        self._counts["potential_conflicts"] += 1
        self._recent.insert(0, result)
        del self._recent[6:]
        return dict(result)

    def preview(self, entity_id: str, action: str, source: str, *,
                when: datetime | None = None) -> dict:
        """Preview arbitration against recent observed intent without storing or dispatching it.

        Entity IDs are inputs only; output contains no identifiable device data.
        This is not a permission check or a substitute for verified device state.
        """
        from .action_policy import simulate

        now = when or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Action preview requires an aware timestamp")
        now = now.astimezone(timezone.utc)
        digest = blake2s(entity_id.encode(), key=self._secret, digest_size=16).hexdigest()
        previous = self._last.get(digest)
        incumbent = None
        if previous is not None:
            stamp, side, old_source = previous
            if not timedelta(0) <= now - stamp <= WINDOW:
                previous = None
        if previous is not None:
            stamp, side, old_source = previous
            incumbent = {"entity_id": entity_id,
                         "action": {"on": "turn_on", "off": "turn_off"}.get(side),
                         "source": old_source, "when": stamp}
        return simulate({"entity_id": entity_id, "action": action, "source": source},
                        incumbent, now=now)

    def summary(self) -> dict:
        return {"mode": "advisory_only", "intents_seen": self._counts["intents"],
                "potential_conflicts": self._counts["potential_conflicts"],
                "recent": [dict(row) for row in self._recent],
                "targets_tracked": len(self._last), "automatic_interventions": 0}
