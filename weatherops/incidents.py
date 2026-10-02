"""Incidents: one per city region at a time, opened and closed with
hysteresis so a storm hovering at the High boundary becomes one incident
with a timeline, not fifty alerts.

Open after two consecutive fresh ticks at High or above. Escalate at
Critical, de-escalate back to High. Resolve after ten observed minutes
below Medium. A flare-up within thirty minutes of resolving reopens the
same incident.
"""
import copy
from collections import defaultdict
from datetime import timedelta

from weatherops.risk import RANK
from weatherops.schema import fmt_ts, parse_ts

SCORE_STEP = 5   # smaller score wobbles are not worth an update event


class IncidentManager:
    def __init__(self, open_ticks=2, resolve_after_s=600, reopen_within_s=1800):
        self.open_ticks = open_ticks
        self.resolve_after = timedelta(seconds=resolve_after_s)
        self.reopen_within = timedelta(seconds=reopen_within_s)
        self.reset()

    def reset(self):
        self._active = {}
        self._recent = {}
        self._streak = defaultdict(int)
        self._calm_since = {}
        self._count = 0

    def active(self):
        return [copy.deepcopy(i) for i in self._active.values()]

    def _note(self, inc, at, status, reason, hazard=None):
        inc["timeline"].append({"at": fmt_ts(at), "status": status,
                                "hazard": hazard or inc["hazard"], "reason": reason})

    def _apply(self, inc, st):
        inc["score"] = st["score"]
        inc["category"] = st["category"]
        inc["routes"] = list(st["routes"])
        inc["hubs"] = list(st["hubs"])
        inc["deliveries_at_risk"] = st["deliveries_at_risk"]
        inc["actions"] = list(st["actions"])
        inc["peak_score"] = max(inc.get("peak_score") or 0, st["score"])
        inc["peak_deliveries"] = max(inc.get("peak_deliveries") or 0, st["deliveries_at_risk"])

    def _open(self, region_id, st, at):
        prev = self._recent.pop(region_id, None)
        status = "escalated" if st["category"] == "critical" else "open"
        if prev and at - parse_ts(prev["resolved_at"]) <= self.reopen_within:
            inc = prev
            inc["resolved_at"] = None
            inc["status"] = status
            inc["hazard"] = st["hazard"]
            self._note(inc, at, status, "reopened")
        else:
            self._count += 1
            inc = {"id": f"INC-{region_id}-{at:%Y%m%d%H%M}-{self._count}", "region": region_id,
                   "region_name": st.get("name", region_id), "hazard": st["hazard"], "status": status,
                   "opened_at": fmt_ts(at), "resolved_at": None, "timeline": []}
            self._note(inc, at, status, f"{st['category']} risk on two consecutive ticks")
        self._apply(inc, st)
        self._active[region_id] = inc
        return inc

    def update(self, regions, observed_at):
        events = []
        for region_id, st in regions.items():
            rank = RANK[st["category"]]
            inc = self._active.get(region_id)

            if inc is None:
                if st["fresh"]:
                    self._streak[region_id] = self._streak[region_id] + 1 if rank >= RANK["high"] else 0
                if self._streak[region_id] >= self.open_ticks:
                    self._streak[region_id] = 0
                    events.append({"type": "opened", "incident": copy.deepcopy(
                        self._open(region_id, st, observed_at))})
                continue

            event = None
            if rank >= RANK["medium"]:
                self._calm_since.pop(region_id, None)
            else:
                since = self._calm_since.setdefault(region_id, observed_at)
                if observed_at - since >= self.resolve_after:
                    inc["status"] = "resolved"
                    inc["resolved_at"] = fmt_ts(observed_at)
                    self._note(inc, observed_at, "resolved", "below medium for 10 minutes")
                    self._recent[region_id] = self._active.pop(region_id)
                    self._calm_since.pop(region_id)
                    events.append({"type": "resolved", "incident": copy.deepcopy(inc)})
                    continue

            if st["category"] == "critical" and inc["status"] != "escalated":
                inc["status"] = "escalated"
                self._note(inc, observed_at, "escalated", "critical risk")
                event = "escalated"
            elif st["category"] == "high" and inc["status"] == "escalated":
                inc["status"] = "open"
                self._note(inc, observed_at, "open", "back to high risk")
                event = "deescalated"

            changed = (abs(st["score"] - inc["score"]) >= SCORE_STEP
                       or st["deliveries_at_risk"] != inc["deliveries_at_risk"]
                       or list(st["actions"]) != inc["actions"]
                       or (st["hazard"] and st["hazard"] != inc["hazard"]))
            if st["hazard"] and st["hazard"] != inc["hazard"]:
                inc["hazard"] = st["hazard"]
                self._note(inc, observed_at, inc["status"], "dominant hazard changed")
            self._apply(inc, st)
            if event or changed:
                events.append({"type": event or "updated", "incident": copy.deepcopy(inc)})
        return events
