"""Dict-backed stand-in for the slice of redis-py the serving layer uses."""
import fnmatch


class FakeRedis:
    def __init__(self):
        self.kv, self.hashes, self.zsets, self.published = {}, {}, {}, []

    # strings
    def set(self, key, value, ex=None):
        self.kv[key] = value

    def get(self, key):
        return self.kv.get(key)

    def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    def incrby(self, key, n):
        self.kv[key] = str(int(self.kv.get(key) or 0) + n)

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.hashes.pop(k, None)
            self.zsets.pop(k, None)

    def keys(self, pattern):
        return [k for k in [*self.kv, *self.hashes, *self.zsets] if fnmatch.fnmatch(k, pattern)]

    # hashes
    def hset(self, key, field=None, value=None, mapping=None):
        h = self.hashes.setdefault(key, {})
        if mapping:
            h.update(mapping)
        if field is not None:
            h[field] = value

    def hdel(self, key, *fields):
        for f in fields:
            self.hashes.get(key, {}).pop(f, None)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    # sorted sets
    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key, lo, hi):
        lo = float("-inf") if lo == "-inf" else float(lo)
        z = self.zsets.get(key, {})
        for member in [m for m, s in z.items() if lo <= s <= float(hi)]:
            del z[member]

    def zrevrange(self, key, start, stop):
        ordered = sorted(self.zsets.get(key, {}).items(), key=lambda ms: -ms[1])
        end = None if stop == -1 else stop + 1
        return [m for m, _ in ordered[start:end]]

    # pub/sub
    def publish(self, channel, message):
        self.published.append((channel, message))

    def ping(self):
        return True

    def pipeline(self, transaction=False):
        return self

    def execute(self):
        return []
