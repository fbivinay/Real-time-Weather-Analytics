from datetime import datetime, timedelta, timezone

from ingestor.timeline import Timeline

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)


def cond(temp, vis=10000):
    return {"temperature_c": temp, "visibility_m": vis, "rain_mmph": 0.0}


def make():
    tl = Timeline()
    tl.add("DEL", T0, cond(10))
    tl.add("DEL", T0 + H, cond(20))
    return tl


def test_interpolates_linearly_between_samples():
    assert make().at("DEL", T0 + H / 2)["temperature_c"] == 15


def test_holds_edge_values_outside_the_range():
    tl = make()
    assert tl.at("DEL", T0 - H)["temperature_c"] == 10
    assert tl.at("DEL", T0 + 5 * H)["temperature_c"] == 20


def test_none_on_either_side_gives_none():
    tl = Timeline()
    tl.add("DEL", T0, cond(10, vis=None))
    tl.add("DEL", T0 + H, cond(20, vis=8000))
    assert tl.at("DEL", T0 + H / 2)["visibility_m"] is None


def test_unknown_city_is_none():
    assert make().at("CHE", T0) is None


def test_out_of_order_adds_are_sorted_and_duplicates_replace():
    tl = Timeline()
    tl.add("DEL", T0 + H, cond(20))
    tl.add("DEL", T0, cond(10))
    tl.add("DEL", T0, cond(12))
    assert tl.span("DEL") == (T0, T0 + H)
    assert tl.at("DEL", T0)["temperature_c"] == 12


def test_exact_sample_time_returns_that_sample():
    assert make().at("DEL", T0 + H)["temperature_c"] == 20
