import numpy as np
import pandas as pd
import pytest

from ml import climatology


def frame(city="CHE", month=1, n=200, rainy_hours=100):
    times = pd.date_range(f"2024-{month:02d}-01", periods=n, freq="h", tz="UTC")
    rain = np.zeros(n)
    rain[:rainy_hours] = np.arange(1, rainy_hours + 1)        # 1..100 mm/h when raining
    return pd.DataFrame({
        "city_id": city, "time": times, "rain_mmph": rain,
        "gust_kmph": np.arange(n) % 100, "temperature_c": 30.0, "humidity_pct": 60.0,
        "visibility_m": 1000.0 + np.arange(n) * 50, "wind_kmph": 10.0, "pressure_hpa": 1008.0,
    })


def test_percentiles_per_city_and_month():
    clim = climatology.build(frame())
    jan = clim["CHE"]["1"]
    assert jan["rain_p95"] == pytest.approx(95.05, abs=0.1)     # rainy hours only
    assert jan["gust_p95"] == pytest.approx(94.05, abs=0.1)
    assert jan["vis_p5"] == pytest.approx(np.percentile(1000.0 + np.arange(200) * 50, 5), abs=1)
    assert jan["heat_p95"] > 30


def test_too_few_rainy_hours_gives_no_rain_percentile():
    clim = climatology.build(frame(rainy_hours=5))
    assert clim["CHE"]["1"]["rain_p95"] is None


def test_months_and_cities_are_separate():
    both = pd.concat([frame("CHE", 1), frame("DEL", 6, rainy_hours=30)])
    clim = climatology.build(both)
    assert set(clim) == {"CHE", "DEL"}
    assert set(clim["DEL"]) == {"6"}
