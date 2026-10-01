"""Is a strange reading broken hardware or real weather?

Each hub has several sensors a few kilometres apart. Real weather moves
them together; a fault moves one. So each sensor is compared with two
things: its own recent history, and the median of the other sensors at the
hub at the same window. Statistics are robust (median and MAD) and counted
in windows of pipeline time - faults happen in real time, whatever the
replay speed.

Verdicts: stale > sensor_suspect (stuck, spike, drift) > weather_event > ok.
"""
import math
import statistics
from datetime import timedelta

FIELDS = ("temp_avg", "humidity_avg", "rain_max", "gust_max", "pressure_avg", "visibility_min")
FLOORS = {"temp_avg": 0.3, "humidity_avg": 2.0, "rain_max": 1.0, "gust_max": 2.5,
          "pressure_avg": 0.3, "visibility_min": 0.05}   # visibility judged on log10
HISTORY = 20
SPIKE_Z = 6
EVENT_Z = 4
NEIGHBOUR_Z = 2
STEADY_Z = 2
STUCK_WINDOWS = 6
DRIFT_BOUNDS = {"temp_avg": 2.5, "pressure_avg": 4.0}
STALE_AFTER = timedelta(seconds=120)


def robust_z(x, history, floor):
    if x is None or len(history) < 3:
        return 0.0
    med = statistics.median(history)
    mad = statistics.median(abs(h - med) for h in history)
    return (x - med) / max(1.4826 * mad, floor)


def _value(window, field):
    v = window.get(field)
    if v is None:
        return None
    return math.log10(max(v, 1.0)) if field == "visibility_min" else v


def _own_z(windows, field):
    if not windows:
        return 0.0
    history = [v for v in (_value(w, field) for w in windows[-HISTORY - 1:-1]) if v is not None]
    return robust_z(_value(windows[-1], field), history, FLOORS[field])


def _stuck(windows):
    last = windows[-STUCK_WINDOWS:]
    if len(last) < STUCK_WINDOWS:
        return False
    if any(w.get("temp_min") != w.get("temp_max") for w in last):
        return False
    keys = {(w.get("temp_avg"), w.get("humidity_avg"), w.get("pressure_avg")) for w in last}
    return len(keys) == 1 and None not in next(iter(keys))


def _residuals(sensor_id, by_start, field):
    """Residual of this sensor against the median of the others, per window
    time, oldest first."""
    out = []
    for start in sorted(by_start):
        row = by_start[start]
        mine = row.get(sensor_id)
        others = [_value(w, field) for sid, w in row.items() if sid != sensor_id]
        others = [v for v in others if v is not None]
        mine = _value(mine, field) if mine else None
        if mine is not None and others:
            out.append(mine - statistics.median(others))
    return out


def verdicts(windows_by_sensor, now, window_s=30):
    out = {}
    live = {}
    for sid, windows in windows_by_sensor.items():
        if not windows or windows[-1]["window_end"] < now - STALE_AFTER:
            out[sid] = {"status": "stale", "reason": None, "field": None}
        else:
            live[sid] = windows

    by_start = {}
    for sid, windows in live.items():
        for w in windows[-HISTORY - 1:]:
            by_start.setdefault(w["window_start"], {})[sid] = w

    own = {sid: {f: _own_z(ws, f) for f in FIELDS} for sid, ws in live.items()}

    for sid, windows in live.items():
        others = [o for o in live if o != sid]
        if _stuck(windows):
            out[sid] = {"status": "sensor_suspect", "reason": "stuck", "field": "temp_avg"}
            continue
        if not others:
            out[sid] = {"status": "ok", "reason": None, "field": None}
            continue

        verdict = None
        for f in FIELDS:
            res = _residuals(sid, by_start, f)
            if not res:
                continue
            res_z = robust_z(res[-1], res[:-1], FLOORS[f])
            others_steady = statistics.median(abs(own[o][f]) for o in others) < STEADY_Z
            if abs(res_z) > SPIKE_Z and others_steady:
                verdict = {"status": "sensor_suspect", "reason": "spike", "field": f}
                break
        if verdict is None:
            for f, bound in DRIFT_BOUNDS.items():
                res = _residuals(sid, by_start, f)
                if len(res) < HISTORY:
                    continue
                recent, earlier = statistics.median(res[-10:]), statistics.median(res[-20:-10])
                if abs(recent) > bound and abs(recent) > abs(earlier):
                    verdict = {"status": "sensor_suspect", "reason": "drift", "field": f}
                    break
        if verdict is None:
            moved = [f for f in FIELDS if abs(own[sid][f]) > EVENT_Z]
            for f in sorted(moved, key=lambda f: -abs(own[sid][f])):
                sign = math.copysign(1, own[sid][f])
                agreeing = sum(1 for o in others
                               if abs(own[o][f]) > NEIGHBOUR_Z and math.copysign(1, own[o][f]) == sign)
                if agreeing >= math.ceil(len(others) / 2):
                    verdict = {"status": "weather_event", "reason": None, "field": f}
                    break
        out[sid] = verdict or {"status": "ok", "reason": None, "field": None}
    return out
