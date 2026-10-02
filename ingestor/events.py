"""Historical events the ingestor can replay through the whole pipeline.

Windows are kept tight around each event's peak so a 120x replay finishes in
minutes. An event stays in this list only if the Historical Forecast data
actually shows its hazard (weather models smooth extremes), and events used
to demo the forecast must fall in the model's held-out 2025 test year.
"""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    start: datetime
    end: datetime
    region_city_ids: tuple
    speed: float = 120


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# Chosen by `python -m ml.scan_events` from 2022-2025 Historical Forecast
# data (strongest multi-city episodes per hazard), not from memory. Windows
# sit around each episode's peak; replay adds a 3-hour pre-roll.
EVENTS = {e.id: e for e in [
    Event("montha-2025", "Cyclone Montha, Andhra coast, Oct 2025",
          _utc(2025, 10, 27, 12), _utc(2025, 10, 29, 0), ("VSK", "VJA", "NLR", "CHE")),
    Event("michaung-2023", "Cyclone Michaung, Chennai, Dec 2023",
          _utc(2023, 12, 3, 12), _utc(2023, 12, 5, 0), ("CHE", "NLR", "VJA")),
    Event("fog-north-2025", "Dense fog, north India, Dec 2025",
          _utc(2025, 12, 28, 12), _utc(2025, 12, 29, 12), ("DEL", "AGR", "LKO", "VNS", "LDH", "CHD")),
    Event("heatwave-2024", "Heatwave, north and east India, May 2024",
          _utc(2024, 5, 28, 21), _utc(2024, 5, 29, 15), ("DEL", "JAI", "AGR", "LKO", "PAT", "KOL", "NAG")),
    Event("gujarat-rain-2024", "Extreme rain, Gujarat, Aug 2024",
          _utc(2024, 8, 26, 0), _utc(2024, 8, 27, 12), ("AMD", "BRD", "SRT", "RJK")),
]}
