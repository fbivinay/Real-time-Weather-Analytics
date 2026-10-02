from datetime import datetime, timedelta, timezone

from weatherops.incidents import IncidentManager

T = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def region(score, cat, hazard="rain", fresh=True, deliveries=100, actions=()):
    return {"name": "Chennai", "score": score, "category": cat, "hazard": hazard, "fresh": fresh,
            "routes": ["LM-CHE-1"], "hubs": ["CHE"], "deliveries_at_risk": deliveries, "actions": list(actions)}


def types(events):
    return [e["type"] for e in events]


def opened(mgr, t=T, cat="high", score=60):
    mgr.update({"CHE": region(score, cat)}, t)
    return mgr.update({"CHE": region(score, cat)}, t + MIN)


def test_one_high_tick_is_not_an_incident():
    mgr = IncidentManager()
    assert mgr.update({"CHE": region(60, "high")}, T) == []
    assert mgr.active() == []


def test_two_fresh_high_ticks_open_an_incident():
    mgr = IncidentManager()
    events = opened(mgr)
    assert types(events) == ["opened"]
    inc = events[0]["incident"]
    assert inc["region"] == "CHE" and inc["status"] == "open" and inc["hazard"] == "rain"
    assert inc["deliveries_at_risk"] == 100
    assert mgr.active()[0]["id"] == inc["id"]


def test_stale_ticks_do_not_count_toward_opening():
    mgr = IncidentManager()
    mgr.update({"CHE": region(60, "high")}, T)
    assert mgr.update({"CHE": region(60, "high", fresh=False)}, T + MIN) == []
    assert types(mgr.update({"CHE": region(60, "high")}, T + 2 * MIN)) == ["opened"]


def test_escalates_at_critical_and_deescalates_back():
    mgr = IncidentManager()
    opened(mgr)
    assert types(mgr.update({"CHE": region(85, "critical")}, T + 2 * MIN)) == ["escalated"]
    assert mgr.active()[0]["status"] == "escalated"
    assert types(mgr.update({"CHE": region(60, "high")}, T + 3 * MIN)) == ["deescalated"]
    assert mgr.active()[0]["peak_score"] == 85


def test_resolves_after_ten_calm_observed_minutes():
    mgr = IncidentManager()
    opened(mgr)
    mgr.update({"CHE": region(10, "low")}, T + 2 * MIN)
    assert mgr.update({"CHE": region(10, "low")}, T + 11 * MIN) == []
    events = mgr.update({"CHE": region(10, "low")}, T + 12 * MIN)
    assert types(events) == ["resolved"]
    assert events[0]["incident"]["resolved_at"] is not None
    assert mgr.active() == []


def test_medium_resets_the_calm_timer():
    mgr = IncidentManager()
    opened(mgr)
    mgr.update({"CHE": region(10, "low")}, T + 2 * MIN)
    mgr.update({"CHE": region(30, "medium")}, T + 8 * MIN)
    assert "resolved" not in types(mgr.update({"CHE": region(10, "low")}, T + 13 * MIN))
    assert mgr.active()


def resolved(mgr):
    opened(mgr)
    mgr.update({"CHE": region(10, "low")}, T + 2 * MIN)
    return mgr.update({"CHE": region(10, "low")}, T + 12 * MIN)[0]["incident"]


def test_flare_up_within_30_minutes_reopens_the_same_incident():
    mgr = IncidentManager()
    first = resolved(mgr)
    t = T + 30 * MIN
    mgr.update({"CHE": region(60, "high")}, t)
    events = mgr.update({"CHE": region(60, "high")}, t + MIN)
    assert types(events) == ["opened"]
    inc = events[0]["incident"]
    assert inc["id"] == first["id"]
    assert inc["resolved_at"] is None
    assert any(e["reason"] == "reopened" for e in inc["timeline"])


def test_flare_up_after_30_minutes_is_a_new_incident():
    mgr = IncidentManager()
    first = resolved(mgr)
    t = T + 43 * MIN
    mgr.update({"CHE": region(60, "high")}, t)
    events = mgr.update({"CHE": region(60, "high")}, t + MIN)
    assert events[0]["incident"]["id"] != first["id"]


def test_updates_only_when_something_operational_changed():
    mgr = IncidentManager()
    opened(mgr)
    assert mgr.update({"CHE": region(62, "high")}, T + 2 * MIN) == []          # small score wobble
    assert types(mgr.update({"CHE": region(62, "high", deliveries=180)}, T + 3 * MIN)) == ["updated"]
    assert types(mgr.update({"CHE": region(62, "high", hazard="wind", deliveries=180)}, T + 4 * MIN)) == ["updated"]
    assert mgr.active()[0]["timeline"][-1]["hazard"] == "wind"


def test_reset_forgets_everything():
    mgr = IncidentManager()
    opened(mgr)
    mgr.reset()
    assert mgr.active() == []
