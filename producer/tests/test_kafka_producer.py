from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kafka_producer as kp


def test_run_produces_five_messages_per_iteration():
    fake_producer = MagicMock()
    kp.run(producer=fake_producer, interval_seconds=0, iterations=1)
    assert fake_producer.send.call_count == 5
    fake_producer.flush.assert_called_once()


def test_run_sends_to_correct_topic():
    fake_producer = MagicMock()
    kp.run(producer=fake_producer, interval_seconds=0, iterations=1)
    for call in fake_producer.send.call_args_list:
        assert call.args[0] == kp.TOPIC_NAME


def test_run_multiple_iterations():
    fake_producer = MagicMock()
    kp.run(producer=fake_producer, interval_seconds=0, iterations=3)
    assert fake_producer.send.call_count == 15
    assert fake_producer.flush.call_count == 3


def test_ensure_topic_exists_creates_topic():
    fake_admin = MagicMock()
    with patch("kafka_producer.KafkaAdminClient", return_value=fake_admin):
        kp.ensure_topic_exists()
    fake_admin.create_topics.assert_called_once()
    fake_admin.close.assert_called_once()


def test_ensure_topic_exists_ignores_already_exists_error():
    fake_admin = MagicMock()
    fake_admin.create_topics.side_effect = kp.TopicAlreadyExistsError("already exists")
    with patch("kafka_producer.KafkaAdminClient", return_value=fake_admin):
        kp.ensure_topic_exists()  # must not raise
    fake_admin.close.assert_called_once()
