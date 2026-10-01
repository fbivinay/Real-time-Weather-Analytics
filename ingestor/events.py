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


EVENTS = {e.id: e for e in [
    Event("michaung-2023", "Cyclone Michaung, Chennai and the Andhra coast",
          _utc(2023, 12, 3, 0), _utc(2023, 12, 5, 0), ("CHE", "NLR", "VJA")),
]}
