"""Reads weather-processed and keeps the dashboard's view of it in Redis.

Kafka keeps the full history; this keeps only what a dashboard asks for -
the latest aggregate per station and a recent-alerts feed - so the API can
answer with key lookups instead of replaying a topic per page load.
"""
import json
import logging
import os

import redis
from kafka import KafkaConsumer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("consumer")

KAFKA_BOOTSTRAP = os.environ.get(
    "KAFKA_BOOTSTRAP", "kafka.weather-pipeline.svc.cluster.local:9092"
)
TOPIC = os.environ.get("TOPIC", "weather-processed")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis-master.weather-pipeline.svc.cluster.local")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
ALERT_FEED_LENGTH = 50


def handle(record, r):
    """Route one record to its Redis key. Returns the key written, for logging."""
    if record["record_type"] == "aggregate":
        key = f"station:{record['station_id']}"
        r.set(key, json.dumps(record))
        return key

    if record["record_type"] == "alert":
        r.lpush("alerts", json.dumps(record))
        # Bounded feed: the dashboard shows a short list and Kafka remains the
        # durable history, so an unbounded Redis list would only grow forever.
        r.ltrim("alerts", 0, ALERT_FEED_LENGTH - 1)
        return "alerts"

    return None


def main():
    r = redis.Redis(
        host=REDIS_HOST, port=6379, password=REDIS_PASSWORD, decode_responses=True
    )
    r.ping()
    logger.info("Connected to Redis at %s", REDIS_HOST)

    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        auto_offset_reset="latest",
        group_id="dashboard-consumer",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    logger.info("Consuming %s from %s", TOPIC, KAFKA_BOOTSTRAP)

    for message in consumer:
        try:
            key = handle(message.value, r)
            if key:
                r.incr("stats:records")
                logger.info("offset=%s -> %s", message.offset, key)
        except Exception:
            # One malformed record must not kill the feed; Kafka still holds it
            # and the next poll continues from the following offset.
            logger.exception("Skipping record at offset %s", message.offset)


if __name__ == "__main__":
    main()
