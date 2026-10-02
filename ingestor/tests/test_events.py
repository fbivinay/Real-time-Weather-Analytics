from datetime import timedelta

from ingestor.events import EVENTS
from weatherops.network import NETWORK


def test_catalogue_events_are_short_valid_windows_over_known_cities():
    assert {"montha-2025", "michaung-2023", "fog-north-2025", "heatwave-2024", "gujarat-rain-2024"} <= set(EVENTS)
    for e in EVENTS.values():
        assert e.start < e.end <= e.start + timedelta(hours=48), e.id
        assert set(e.region_city_ids) <= set(NETWORK.cities), e.id
        assert e.start.year >= 2022 and e.end.year <= 2025


def test_a_forecast_demo_event_lies_in_the_held_out_2025_test_year():
    assert EVENTS["montha-2025"].start.year == 2025
    assert EVENTS["fog-north-2025"].start.year == 2025
