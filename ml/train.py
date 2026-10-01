"""Train the +60 min LightGBM nowcast, judge it against persistence, and write
ml/artifacts/ (gzipped models + model_card.json) and ml/report.md.

Split by time, never shuffled: train 2022-01 -> 2024-06, validate 2024-07 ->
2024-12 (early stopping), test on all of 2025. A target ships only if it
beats "no change" on the test year; otherwise the engine uses persistence
for it and the model card says so.

    python -m ml.train
"""
import gzip
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml import dataset
from ml.climatology import load_history
from weatherops import forecast, risk

ARTIFACTS = Path(__file__).with_name("artifacts")
REPORT = Path(__file__).with_name("report.md")
SPLITS = {"train": ("2022-01-01", "2024-07-01"), "valid": ("2024-07-01", "2025-01-01"),
          "test": ("2025-01-01", "2026-01-01")}
PARAMS = {"num_leaves": 31, "learning_rate": 0.05, "min_data_in_leaf": 200, "feature_fraction": 0.9,
          "bagging_fraction": 0.8, "bagging_freq": 1, "seed": 7, "verbose": -1, "num_threads": 4}
OBJECTIVE = {"rain_mmph": {"objective": "tweedie", "tweedie_variance_power": 1.3},
             "gust_kmph": {"objective": "regression"},
             "temperature_c": {"objective": "regression"},
             "visibility_m": {"objective": "regression"}}
ROUNDS, PATIENCE = 1500, 50   # early stopping on 2024-H2 decides; the cap is a backstop
RAINY = 0.1


def ship(skill):
    return skill is not None and skill > 0


def _mask(meta, split):
    lo, hi = SPLITS[split]
    t = meta["time"]
    return (t >= pd.Timestamp(lo, tz="UTC")) & (t < pd.Timestamp(hi, tz="UTC"))


def _mae(a, b):
    return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))


def _inputs(rain, gust, temp, rh, vis_log):
    """Hourly risk inputs; accumulation left out on both sides of the
    comparison, as the engine's forecast term does."""
    return {"rain_mmph": rain, "rain_accum_mm": None, "gust_kmph": gust, "temperature_c": temp,
            "humidity_pct": rh, "visibility_m": None if math.isnan(vis_log) else 10 ** vis_log}


def onset_metrics(X, y, preds):
    """Onsets: below High now, High or above an hour later. Persistence can
    never call one (it predicts "now"), so recall is the model's edge."""
    high = risk.RANK["high"]
    tp = fp = fn = 0
    for i in range(len(X)):
        row = X.iloc[i]
        now = risk.assess(_inputs(row["rain_mmph_now"], row["gust_kmph_now"], row["temperature_c_now"],
                                  row["humidity_pct_now"], row["visibility_m_now"]))
        if risk.RANK[now["category"]] >= high:
            continue
        actual = risk.assess(_inputs(y["rain_mmph"].iloc[i], y["gust_kmph"].iloc[i], y["temperature_c"].iloc[i],
                                     row["humidity_pct_now"], y["visibility_m"].iloc[i]))
        predicted = risk.assess(_inputs(preds["rain_mmph"][i], preds["gust_kmph"][i], preds["temperature_c"][i],
                                        row["humidity_pct_now"], preds["visibility_m"][i]))
        hit, called = risk.RANK[actual["category"]] >= high, risk.RANK[predicted["category"]] >= high
        tp += hit and called
        fp += called and not hit
        fn += hit and not called
    return {"onsets": tp + fn, "predicted": tp + fp, "true_positives": tp,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


def main():
    frame = load_history()
    X, y, meta = dataset.build(frame)
    X = X.astype("float32")
    split = {s: _mask(meta, s) for s in SPLITS}
    ARTIFACTS.mkdir(exist_ok=True)

    card = {"version": datetime.now(timezone.utc).strftime("%Y%m%d"), "trained_at": datetime.now(timezone.utc).isoformat(),
            "data": {"source": "Open-Meteo Historical Forecast API", "cities": int(meta["city_id"].nunique()),
                     "rows": int(len(X)), "period": [str(meta["time"].min()), str(meta["time"].max())]},
            "split": SPLITS, "horizon_minutes": 60, "features": list(forecast.FEATURES), "targets": {}}
    test_preds = {}
    for target in forecast.TARGETS:
        ok = {s: split[s] & y[target].notna() for s in SPLITS}
        train = lgb.Dataset(X[ok["train"]], y.loc[ok["train"], target])
        valid = lgb.Dataset(X[ok["valid"]], y.loc[ok["valid"], target], reference=train)
        booster = lgb.train({**PARAMS, **OBJECTIVE[target]}, train, ROUNDS, valid_sets=[valid],
                            callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
        test_mask = split["test"]
        pred_all = booster.predict(X[test_mask], num_iteration=booster.best_iteration)
        truth = y.loc[test_mask, target]
        present = truth.notna().to_numpy()
        now = X.loc[test_mask, f"{target}_now"].to_numpy()
        valid_now = present & ~np.isnan(now)
        mae_model = _mae(pred_all[valid_now], truth[valid_now])
        mae_persist = _mae(now[valid_now], truth[valid_now])
        skill = 1 - mae_model / mae_persist if mae_persist else None
        entry = {"shipped": ship(skill), "objective": OBJECTIVE[target]["objective"],
                 "best_iteration": int(booster.best_iteration), "mae": round(mae_model, 4),
                 "mae_persistence": round(mae_persist, 4), "skill": None if skill is None else round(skill, 4),
                 "units": "log10 m" if target == "visibility_m" else None}
        if target == "rain_mmph":
            rainy = valid_now & (truth.to_numpy() >= RAINY)
            entry["mae_rainy_hours"] = round(_mae(pred_all[rainy], truth[rainy]), 4)
            entry["mae_rainy_hours_persistence"] = round(_mae(now[rainy], truth[rainy]), 4)
        if entry["shipped"]:
            entry["file"] = f"{target}.txt.gz"
            text = booster.model_to_string(num_iteration=booster.best_iteration)
            (ARTIFACTS / entry["file"]).write_bytes(gzip.compress(text.encode(), compresslevel=9))
        card["targets"][target] = entry
        test_preds[target] = np.where(np.isnan(pred_all), now, pred_all) if entry["shipped"] else now
        print(target, entry, flush=True)

    # Onset evaluation on a deterministic 20% sample of the test year (the
    # per-row risk scoring is pure Python).
    test_idx = np.flatnonzero(split["test"].to_numpy())
    sample = test_idx[::5]
    pos = {k: v for k, v in zip(test_idx, range(len(test_idx)))}
    Xs, ys = X.iloc[sample], y.iloc[sample]
    preds = {t: np.array([test_preds[t][pos[i]] for i in sample]) for t in forecast.TARGETS}
    persistence = {t: Xs[f"{t}_now"].to_numpy() for t in forecast.TARGETS}
    card["onset"] = {"model": onset_metrics(Xs, ys, preds), "persistence": onset_metrics(Xs, ys, persistence),
                     "sample": "every 5th test-year row"}
    (ARTIFACTS / "model_card.json").write_text(json.dumps(card, indent=2))
    REPORT.write_text(render_report(card))
    size = sum(p.stat().st_size for p in ARTIFACTS.iterdir())
    print(f"artifacts {size / 1024:.0f} KB; onset {card['onset']}")


def render_report(card):
    rows = []
    for t, e in card["targets"].items():
        rows.append(f"| {t} | {e['mae']} | {e['mae_persistence']} | {e['skill']} | "
                    f"{'yes' if e['shipped'] else 'no - persistence used'} |")
    rain = card["targets"]["rain_mmph"]
    m, p = card["onset"]["model"], card["onset"]["persistence"]
    return f"""# Forecast model report

Generated by `python -m ml.train` on {card['trained_at'][:10]}.

**Task:** predict conditions 60 minutes ahead for each of {card['data']['cities']} Indian cities, then score
risk from the prediction. **Data:** {card['data']['source']}, hourly, {card['data']['rows']:,} rows,
{card['data']['period'][0][:10]} to {card['data']['period'][1][:10]}. **Split by time:** train
{card['split']['train'][0]} to {card['split']['train'][1]}, validate to {card['split']['valid'][1]}
(early stopping), test on {card['split']['test'][0][:4]}.

**Baseline:** persistence - the value an hour from now equals the value now. Skill = 1 - MAE(model) /
MAE(persistence); above 0 means the model helps.

| Target | MAE model | MAE persistence | Skill | Shipped |
|---|---|---|---|---|
{chr(10).join(rows)}

Visibility is modelled and scored as log10 metres. Rain on rainy hours only (>= 0.1 mm/h):
model MAE {rain.get('mae_rainy_hours')} vs persistence {rain.get('mae_rainy_hours_persistence')}.

## Does it warn earlier?

Onset = a city below High risk now that is at High or above an hour later (intensity factors;
{card['onset']['sample']}). Persistence predicts "no change", so it can never call an onset.

| | Onsets | Called | Correct | Precision | Recall |
|---|---|---|---|---|---|
| Model | {m['onsets']} | {m['predicted']} | {m['true_positives']} | {m['precision']} | {m['recall']} |
| Persistence | {p['onsets']} | {p['predicted']} | {p['true_positives']} | {p['precision']} | {p['recall']} |

## Limits

- One hour ahead only: the history is hourly, so a 30-minute model would just interpolate.
- No neighbouring-city features yet - a storm approaching from upwind is invisible until it arrives.
- Trained on model analysis data (Open-Meteo), not station observations; it learns the weather model's
  short-term dynamics, which smooth extremes.
- No NWP forecasts as inputs: there is no free archive of what was forecast at each moment, so training
  on them would leak the answer.
"""


if __name__ == "__main__":
    main()
