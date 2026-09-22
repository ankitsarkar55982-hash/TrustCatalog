"""
Unsupervised anomaly detection with Isolation Forest.

Two details that matter:

1. The model is fitted on "mostly normal" sellers (those below the
   NORMAL_TRAINING_PERCENTILE of the rule-based behavioural score), so bad
   sellers do not teach the model that being bad is normal.

2. The raw score is converted to 0-100 against a REFERENCE DISTRIBUTION saved
   with the model. If we re-ranked against whatever population we happen to be
   scoring, a batch of 10 sellers would always contain a "worst" seller at 100.
   The stress test depends on this being stable.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
import joblib

import config


def _matrix(features, feature_names):
    """Build the numeric matrix, tolerating missing columns."""
    X = pd.DataFrame(index=features.index)
    for name in feature_names:
        if name in features.columns:
            X[name] = pd.to_numeric(features[name], errors="coerce")
        else:
            X[name] = 0.0
    return X.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype="float64")


def train_model(features, behavioral_score=None, feature_names=None, verbose=True):
    """
    Fit the Isolation Forest and return a bundle dict (not just the model).
    """
    feature_names = list(feature_names or config.ANOMALY_FEATURES)

    if features is None or features.empty:
        raise ValueError("Cannot train the anomaly model on an empty feature table.")

    X_all = _matrix(features, feature_names)

    # Select the "normal" training subset.
    if behavioral_score is not None and len(behavioral_score) == len(features):
        cutoff = np.percentile(
            np.asarray(behavioral_score, dtype="float64"),
            config.NORMAL_TRAINING_PERCENTILE,
        )
        mask = np.asarray(behavioral_score, dtype="float64") <= cutoff
    else:
        mask = np.ones(len(features), dtype=bool)

    # Never train on a handful of rows.
    if mask.sum() < max(20, len(features) * 0.2):
        mask = np.ones(len(features), dtype=bool)

    X_train = X_all[mask]

    params = dict(config.IFOREST_PARAMS)
    # max_samples must not exceed the number of training rows.
    if isinstance(params.get("max_samples"), int):
        params["max_samples"] = min(params["max_samples"], len(X_train))

    pipeline = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("iforest", IsolationForest(**params)),
        ]
    )
    pipeline.fit(X_train)

    # Reference distribution for stable normalisation: score the FULL table.
    raw_reference = -pipeline.named_steps["iforest"].score_samples(
        pipeline.named_steps["scaler"].transform(X_all)
    )

    bundle = {
        "pipeline": pipeline,
        "feature_names": feature_names,
        "reference_scores": np.sort(raw_reference),
        "n_training_rows": int(mask.sum()),
        "n_total_rows": int(len(features)),
        "version": "1.0.0",
    }
    if verbose:
        print(
            f"  Isolation Forest trained on {bundle['n_training_rows']} "
            f"'normal' sellers out of {bundle['n_total_rows']}."
        )
    return bundle


def save_model(bundle, path=None):
    path = path or config.MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    return path


def load_model(path=None):
    """Return the bundle, or None when the file is missing/corrupt."""
    path = path or config.MODEL_PATH
    if not path.exists():
        return None
    try:
        bundle = joblib.load(path)
    except Exception as exc:                       # noqa: BLE001
        print(f"  [warn] could not load model at {path}: {exc}")
        return None
    if not isinstance(bundle, dict) or "pipeline" not in bundle:
        print(f"  [warn] model file at {path} has an unexpected format - retrain it.")
        return None
    return bundle


def score(features, bundle):
    """
    Add `anomaly_score` (0-100, higher = more anomalous) and `is_anomaly`
    (1/0) to a copy of the feature table.
    """
    out = features.copy()
    if out.empty:
        out["anomaly_score"] = []
        out["is_anomaly"] = []
        return out

    X = _matrix(out, bundle["feature_names"])
    pipeline = bundle["pipeline"]

    raw = -pipeline.named_steps["iforest"].score_samples(
        pipeline.named_steps["scaler"].transform(X)
    )

    reference = np.asarray(bundle["reference_scores"], dtype="float64")
    if reference.size == 0:
        normalised = np.full(len(raw), 50.0)
    else:
        # Percentile of each score inside the saved reference distribution.
        positions = np.searchsorted(reference, raw, side="right")
        normalised = 100.0 * positions / float(reference.size)

    out["anomaly_score"] = np.clip(normalised, 0.0, 100.0)
    out["is_anomaly"] = (pipeline.predict(X) == -1).astype(int)
    return out
