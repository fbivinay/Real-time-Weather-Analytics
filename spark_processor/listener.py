"""Streaming-query progress -> Redis, so the dashboard's health panel can show
throughput, batch time, Kafka backlog and what the watermark and dedup
dropped - without Prometheus, and without the Spark UI being exposed."""
import json
import logging

log = logging.getLogger("processor.listener")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def digest(progress):
    """StreamingQueryProgress JSON (dict) -> the fields the health panel uses."""
    state = progress.get("stateOperators") or []
    sources = progress.get("sources") or []
    metrics = (sources[0].get("metrics") or {}) if sources else {}
    return {
        "name": progress.get("name"),
        "batch": progress.get("batchId"),
        "at": progress.get("timestamp"),
        "rows": progress.get("numInputRows", 0),
        "input_rps": round(_num(progress.get("inputRowsPerSecond")), 2),
        "processed_rps": round(_num(progress.get("processedRowsPerSecond")), 2),
        "batch_ms": (progress.get("durationMs") or {}).get("triggerExecution", 0),
        "watermark": (progress.get("eventTime") or {}).get("watermark"),
        "offsets_behind": _num(metrics.get("avgOffsetsBehindLatest")),
        "dropped_late": sum(s.get("numRowsDroppedByWatermark", 0) for s in state),
        "dropped_duplicates": sum(int((s.get("customMetrics") or {}).get("numDroppedDuplicateRows", 0))
                                  for s in state),
    }


def make_listener(client, ttl=300):
    """Built lazily so digest() stays importable without pyspark."""
    from pyspark.sql.streaming import StreamingQueryListener

    class RedisProgressListener(StreamingQueryListener):
        def onQueryStarted(self, event):
            pass

        def onQueryProgress(self, event):
            # Metrics must never take the stream down with them.
            try:
                d = digest(json.loads(event.progress.json))
                client.set(f"health:spark:{d['name']}", json.dumps(d), ex=ttl)
                if d["dropped_late"]:
                    client.incrby("dq:dropped_late", d["dropped_late"])
                if d["dropped_duplicates"]:
                    client.incrby("dq:dropped_duplicates", d["dropped_duplicates"])
            except Exception:
                log.exception("could not publish query progress")

        def onQueryIdle(self, event):
            pass

        def onQueryTerminated(self, event):
            try:
                client.set(f"health:spark:terminated:{event.id}",
                           json.dumps({"id": str(event.id), "exception": event.exception}), ex=ttl)
            except Exception:
                log.exception("could not publish query termination")

    return RedisProgressListener()
