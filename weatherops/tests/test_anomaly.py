import random
from datetime import datetime, timedelta, timezone

from weatherops import anomaly

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
W = timedelta(seconds=30)
N = 21


def make_windows(seed, n=N, temp=28.0, rain=0.0, start=T0):
    rand = random.Random(seed)
    out = []
    for k in range(n):
        t = temp + rand.gauss(0, 0.2)
        out.append({
            "window_start": start + k * W, "window_end": start + (k + 1) * W,
            "temp_min": round(t - 0.2, 1), "temp_avg": round(t, 2), "temp_max": round(t + 0.2, 1),
            "humidity_avg": round(70 + rand.gauss(0, 1.2), 2),
            "rain_max": round(max(0.0, rain + rand.gauss(0, 0.3)), 2),
            "gust_max": round(20 + rand.gauss(0, 2.0), 2),
            "pressure_avg": round(1008 + rand.gauss(0, 0.2), 2),
            "visibility_min": round(10000 * (1 + rand.gauss(0, 0.03))),
        })
    return out


def hub(n_sensors=4):
    return {f"CHE-S{i + 1}": make_windows(i) for i in range(n_sensors)}


NOW = T0 + N * W + timedelta(seconds=20)


def statuses(v):
    return {sid: x["status"] for sid, x in v.items()}


def test_healthy_hub_is_all_ok():
    assert set(statuses(anomaly.verdicts(hub(), NOW)).values()) == {"ok"}


def test_isolated_spike_is_a_suspect_sensor():
    h = hub()
    h["CHE-S2"][-1]["rain_max"] = 150.0
    v = anomaly.verdicts(h, NOW)
    assert v["CHE-S2"] == {"status": "sensor_suspect", "reason": "spike", "field": "rain_max"}
    assert all(v[s]["status"] == "ok" for s in ("CHE-S1", "CHE-S3", "CHE-S4"))


def test_shared_jump_is_weather():
    h = hub()
    for windows in h.values():
        windows[-1]["rain_max"] = 30.0 + random.Random(1).random()
    v = anomaly.verdicts(h, NOW)
    assert set(statuses(v).values()) == {"weather_event"}
    assert v["CHE-S1"]["field"] == "rain_max"


def freeze(windows, count):
    frozen = windows[-count - 1]
    for w in windows[-count:]:
        for f in ("temp_avg", "humidity_avg", "pressure_avg"):
            w[f] = frozen[f]
        w["temp_min"] = w["temp_max"] = frozen["temp_avg"]


def test_six_identical_windows_is_stuck():
    h = hub()
    freeze(h["CHE-S3"], 6)
    assert anomaly.verdicts(h, NOW)["CHE-S3"]["reason"] == "stuck"


def test_five_identical_windows_is_not_yet_stuck():
    h = hub()
    freeze(h["CHE-S3"], 4)   # the source window plus four copies = five identical
    assert anomaly.verdicts(h, NOW)["CHE-S3"]["status"] == "ok"


def test_growing_offset_is_drift():
    h = hub()
    for k, w in enumerate(h["CHE-S4"][-20:]):
        w["temp_avg"] += 4.0 * (k + 1) / 20
    v = anomaly.verdicts(h, NOW)
    assert v["CHE-S4"] == {"status": "sensor_suspect", "reason": "drift", "field": "temp_avg"}


def test_silent_sensor_is_stale():
    h = hub()
    h["CHE-S1"] = h["CHE-S1"][:-6]   # last window ended three minutes before now
    assert anomaly.verdicts(h, NOW)["CHE-S1"]["status"] == "stale"
    assert anomaly.verdicts({"CHE-S9": []}, NOW)["CHE-S9"]["status"] == "stale"


def test_lone_sensor_is_never_called_a_spike():
    h = {"CHE-S1": make_windows(0)}
    h["CHE-S1"][-1]["rain_max"] = 150.0
    assert anomaly.verdicts(h, NOW)["CHE-S1"]["status"] != "sensor_suspect"


def test_robust_z_uses_the_floor_on_flat_history():
    assert anomaly.robust_z(5.0, [0.0] * 10, floor=1.0) == 5.0
    assert anomaly.robust_z(5.0, [0.0, 0.0], floor=1.0) == 0.0   # too little history to judge
