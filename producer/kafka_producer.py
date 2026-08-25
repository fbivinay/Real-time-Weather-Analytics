import json
import logging
import time

from kafka import KafkaProducer, KafkaAdminClient
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError

import weather_generator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kafka_producer")

BOOTSTRAP_SERVERS = "kafka.weather-pipeline.svc.cluster.local:9092"
TOPIC_NAME = "weather-data"
PRODUCE_INTERVAL_SECONDS = 5


def ensure_topic_exists(bootstrap_servers=BOOTSTRAP_SERVERS, topic_name=TOPIC_NAME):
    admin = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        admin.create_topics([NewTopic(name=topic_name, num_partitions=1, replication_factor=1)])
        logger.info("Created topic %s", topic_name)
    except TopicAlreadyExistsError:
        logger.info("Topic %s already exists, reusing it", topic_name)
    finally:
        admin.close()


def build_producer(bootstrap_servers=BOOTSTRAP_SERVERS):
    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


def run(producer=None, interval_seconds=PRODUCE_INTERVAL_SECONDS, iterations=None):
    if producer is None:
        producer = build_producer()

    count = 0
    while iterations is None or count < iterations:
        readings = weather_generator.generate_all_readings()
        for reading in readings:
            producer.send(TOPIC_NAME, value=reading)
        producer.flush()
        logger.info("Produced %d messages", len(readings))
        count += 1
        if iterations is None or count < iterations:
            time.sleep(interval_seconds)


if __name__ == "__main__":
    ensure_topic_exists()
    run()
