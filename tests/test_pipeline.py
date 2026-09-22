"""
Tests for TrustCatalog.

Run either way:
    python tests/test_pipeline.py      (no pytest needed)
    pytest tests/                      (if you have pytest installed)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import config
from src import (
    anomaly_detection,
    feature_engineering as fe,
    ghost_listing,
    preprocessing,
    risk_scoring,
    stress_test,
)


def _fake_seller_orders(n_sellers=30, n_orders_each=20, seed=0):
    """Tiny synthetic seller-order table with a few obviously bad sellers."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-01")
    rows = []
    for s in range(n_sellers):
        bad = s < 5
        for o in range(n_orders_each):
            purchase = start + pd.Timedelta(days=int(rng.integers(0, 120)))
            cancelled = rng.random() < (0.30 if bad else 0.03)
            delivered = not cancelled
            late = delivered and rng.random() < (0.40 if bad else 0.05)
            rows.append(
                {
                    "seller_id": f"S{s:03d}",
                    "order_id": f"S{s:03d}-O{o:04d}",
                    "order_status": "canceled" if cancelled else "delivered",
                    "order_purchase_timestamp": purchase,
                    "order_approved_at": purchase + pd.Timedelta(hours=6),
                    "order_delivered_carrier_date": purchase + pd.Timedelta(days=1),
                    "order_delivered_customer_date": (
                        purchase + pd.Timedelta(days=10) if delivered else pd.NaT
                    ),
                    "order_estimated_delivery_date": purchase + pd.Timedelta(days=14),
                    "delivery_days": 10.0 if delivered else np.nan,
                    "shipping_delay": 6.0 if bad else 0.5,
                    "delivery_lateness": 2.0 if late else -2.0,
                    "is_failed": cancelled,
                    "is_cancelled": cancelled,
                    "is_unavailable": False,
                    "is_delivered": delivered,
                    "is_late": late,
                    "shipping_limit_breach": bad,
                    "never_shipped": cancelled,
                    "review_score": 2.0 if bad else 4.6,
                    "n_products_in_order": 1,
                    "price": 100.0,
                }
            )
    return pd.DataFrame(rows)


def test_window_split_is_disjoint():
    df = _fake_seller_orders()
    feature_df, outcome_df, cutoff = preprocessing.split_windows(df)
    assert feature_df["order_purchase_timestamp"].max() < cutoff
    assert outcome_df["order_purchase_timestamp"].min() >= cutoff
    assert len(feature_df) + len(outcome_df) == len(df)
    print("  window split is disjoint and complete")


def test_features_have_no_outcome_columns():
    df = _fake_seller_orders()
    feature_df, outcome_df, cutoff = preprocessing.split_windows(df)
    features = fe.build_seller_features(feature_df, cutoff)
    leaked = [c for c in features.columns if c.startswith("outcome_") or c == "label"]
    assert not leaked, f"outcome columns leaked into features: {leaked}"
    assert not features.isna().any().any(), "features contain NaN"
    print("  feature table is leak-free and NaN-free")


def test_rates_are_bounded():
    df = _fake_seller_orders()
    feature_df, _, cutoff = preprocessing.split_windows(df)
    features = fe.build_seller_features(feature_df, cutoff)
    for col in ("cancellation_rate", "late_delivery_rate", "failed_order_rate"):
        assert features[col].between(0, 1).all(), f"{col} out of [0,1]"
    print("  all rate features stay inside [0, 1]")


def test_risk_score_bounds_and_ordering():
    df = _fake_seller_orders()
    feature_df, _, cutoff = preprocessing.split_windows(df)
    features = fe.build_seller_features(feature_df, cutoff)

    behavioral = risk_scoring.compute_behavioral_score(features)
    bundle = anomaly_detection.train_model(features, behavioral, verbose=False)
    features = anomaly_detection.score(features, bundle)
    features = ghost_listing.add_ghost_scores(features)
    features = risk_scoring.compute_risk(features)

    assert features["risk_score"].between(0, 100).all()
    assert features["ghost_listing_score"].between(0, 100).all()
    assert features["anomaly_score"].between(0, 100).all()

    bad = features[features["seller_id"].isin([f"S{i:03d}" for i in range(5)])]
    good = features[~features["seller_id"].isin([f"S{i:03d}" for i in range(5)])]
    assert bad["risk_score"].mean() > good["risk_score"].mean() + 15, (
        "the known-bad sellers did not score clearly higher"
    )
    print(f"  risk scores bounded; bad sellers {bad['risk_score'].mean():.1f} "
          f"vs good {good['risk_score'].mean():.1f}")


def test_risk_levels_match_bands():
    scores = [0, 30, 31, 60, 61, 80, 81, 100]
    expected = ["LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH", "CRITICAL", "CRITICAL"]
    got = [risk_scoring.risk_level(s) for s in scores]
    assert got == expected, f"{got} != {expected}"
    print("  risk bands map correctly")


def test_model_roundtrip(tmp_path=None):
    df = _fake_seller_orders()
    feature_df, _, cutoff = preprocessing.split_windows(df)
    features = fe.build_seller_features(feature_df, cutoff)
    bundle = anomaly_detection.train_model(features, verbose=False)

    target = Path(tmp_path) if tmp_path else config.MODELS_DIR / "_test_model.joblib"
    if tmp_path:
        target = Path(tmp_path) / "model.joblib"
    anomaly_detection.save_model(bundle, target)

    loaded = anomaly_detection.load_model(target)
    assert loaded is not None
    a = anomaly_detection.score(features, bundle)["anomaly_score"].to_numpy()
    b = anomaly_detection.score(features, loaded)["anomaly_score"].to_numpy()
    assert np.allclose(a, b), "scores changed after save/load"
    target.unlink(missing_ok=True)
    print("  model save/load reproduces identical scores")


def test_empty_inputs_do_not_crash():
    empty = pd.DataFrame()
    assert fe.build_seller_features(empty, pd.Timestamp("2024-01-01")).empty
    assert ghost_listing.compute_ghost_score(empty).empty
    assert risk_scoring.compute_behavioral_score(empty).empty
    assert fe.build_outcome_labels(empty).empty
    print("  empty inputs return empty results instead of raising")


def test_stress_test_detects_degraded():
    df = _fake_seller_orders()
    feature_df, _, cutoff = preprocessing.split_windows(df)
    features = fe.build_seller_features(feature_df, cutoff)
    behavioral = risk_scoring.compute_behavioral_score(features)
    bundle = anomaly_detection.train_model(features, behavioral, verbose=False)

    summary, detail = stress_test.run_stress_test(features, bundle)
    assert summary["n_degraded"] > 0
    assert summary["avg_risk_degraded"] > summary["avg_risk_normal"], (
        "degraded sellers did not score above normal sellers"
    )
    print(f"  stress test: {summary['n_detected']}/{summary['n_degraded']} detected, "
          f"{summary['false_alarms']} false alarms")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    print("Running TrustCatalog tests\n")
    for test in tests:
        name = test.__name__
        try:
            test()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:                    # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        print()
    print(f"{len(tests) - failed}/{len(tests)} tests passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
