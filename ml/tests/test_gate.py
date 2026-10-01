import gzip
import json

import lightgbm as lgb
import numpy as np

from ml.train import ship
from weatherops import forecast


def test_a_target_ships_only_if_it_beats_persistence():
    assert ship(0.01) is True
    assert ship(0.0) is False
    assert ship(-0.2) is False
    assert ship(None) is False


def test_forecaster_falls_back_to_persistence_for_unshipped_targets(tmp_path):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(300, len(forecast.FEATURES)))
    booster = lgb.train({"objective": "regression", "verbose": -1, "num_leaves": 4},
                        lgb.Dataset(X, X[:, 0] * 2 + 5), num_boost_round=5)
    (tmp_path / "gust_kmph.txt.gz").write_bytes(gzip.compress(booster.model_to_string().encode()))
    card = {"targets": {"gust_kmph": {"shipped": True, "file": "gust_kmph.txt.gz"},
                        "rain_mmph": {"shipped": False}, "temperature_c": {"shipped": False},
                        "visibility_m": {"shipped": False}}}
    (tmp_path / "model_card.json").write_text(json.dumps(card))

    fc = forecast.Forecaster.load(tmp_path)
    now = {"rain_mmph": 3.0, "gust_kmph": 20.0, "temperature_c": 28.0, "visibility_m": 9000.0, "humidity_pct": 80.0}
    [ahead] = fc.predict([X[0].tolist()], [now])
    assert ahead["rain_mmph"] == 3.0 and ahead["temperature_c"] == 28.0     # persistence
    assert ahead["gust_kmph"] != 20.0                                        # model
    assert ahead["humidity_pct"] == 80.0


def test_missing_card_means_no_forecaster(tmp_path):
    assert forecast.Forecaster.load(tmp_path) is None
