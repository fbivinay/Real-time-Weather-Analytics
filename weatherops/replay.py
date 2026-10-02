"""The simulation clock. Operations are simulated; their weather is the real
hourly rain of a past monsoon window, replayed at SPEED x wall-clock time.
When the window ends the replay starts over (the engine resets live state).
"""
from datetime import datetime, timedelta, timezone

from weatherops.events import parse_ts

REPLAY_START = "2025-07-01T00:00:00Z"   # history runs up to the day before
REPLAY_DAYS = 45
SPEED = 60


class SimClock:
    def __init__(self, start=REPLAY_START, days=REPLAY_DAYS, speed=SPEED, offset_s=0.0, real_start=None):
        self.start = parse_ts(start)
        self.window_s = days * 86400
        self.speed = speed
        self.offset_s = offset_s % self.window_s       # sim seconds already elapsed
        self.real_start = real_start or datetime.now(timezone.utc)

    def elapsed_s(self, real_now=None):
        real_now = real_now or datetime.now(timezone.utc)
        return (self.offset_s + (real_now - self.real_start).total_seconds() * self.speed) % self.window_s

    def now(self, real_now=None):
        return self.start + timedelta(seconds=self.elapsed_s(real_now))

    def lap(self, real_now=None):
        real_now = real_now or datetime.now(timezone.utc)
        return int((self.offset_s + (real_now - self.real_start).total_seconds() * self.speed) // self.window_s)
