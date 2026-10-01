import json
from pathlib import Path

from spark_processor import listener

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "progress.json").read_text())


def test_digest_extracts_rates_drops_and_backlog():
    d = listener.digest(FIXTURE)
    assert d["name"] == "features-kafka"
    assert d["batch"] == 42
    assert d["rows"] == 160
    assert d["input_rps"] == 16.0
    assert d["processed_rps"] == 80.5
    assert d["batch_ms"] == 1987
    assert d["watermark"] == "2026-10-01T09:59:50.000Z"
    assert d["dropped_late"] == 4          # summed across both stateful operators
    assert d["dropped_duplicates"] == 5
    assert d["offsets_behind"] == 12.0


def test_digest_of_a_stateless_query_without_sources_metrics_is_zeroed():
    d = listener.digest({"name": "raw-s3", "batchId": 1, "timestamp": "t", "numInputRows": 0})
    assert d["dropped_late"] == 0
    assert d["dropped_duplicates"] == 0
    assert d["offsets_behind"] == 0
    assert d["input_rps"] == 0
    assert d["batch_ms"] == 0
