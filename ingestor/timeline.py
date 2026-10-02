"""Per-city time series of Conditions with linear interpolation, so hourly
history can be read at any instant of a compressed replay clock."""
import bisect


class Timeline:
    def __init__(self):
        self._times = {}
        self._values = {}

    def add(self, city_id, t, cond):
        times = self._times.setdefault(city_id, [])
        values = self._values.setdefault(city_id, [])
        i = bisect.bisect_left(times, t)
        if i < len(times) and times[i] == t:
            values[i] = cond
        else:
            times.insert(i, t)
            values.insert(i, cond)

    def span(self, city_id):
        times = self._times.get(city_id)
        return (times[0], times[-1]) if times else None

    def at(self, city_id, t):
        times = self._times.get(city_id)
        if not times:
            return None
        values = self._values[city_id]
        i = bisect.bisect_left(times, t)
        if i < len(times) and times[i] == t:
            return dict(values[i])
        if i == 0:
            return dict(values[0])
        if i == len(times):
            return dict(values[-1])
        t0, t1 = times[i - 1], times[i]
        f = (t - t0) / (t1 - t0)
        a, b = values[i - 1], values[i]
        return {k: (None if a.get(k) is None or b.get(k) is None else a[k] + (b[k] - a[k]) * f)
                for k in a}
