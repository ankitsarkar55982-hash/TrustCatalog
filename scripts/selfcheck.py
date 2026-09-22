"""
Self-check: verify the project is wired up correctly BEFORE the demo.

Run:  python scripts/selfcheck.py

It checks, in order:
  1. every dependency imports
  2. a dataset is reachable (real or demo)
  3. the SQLite database exists and holds the expected tables
  4. every column the dashboard reads actually exists   <- catches the classic
     "works in the pipeline, KeyError in Streamlit" failure
  5. the saved model loads and can score
  6. the stress test runs end to end

Exit code 0 = safe to demo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import config

FAILURES = []
WARNINGS = []


def ok(msg):
    print(f"  [ok]   {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")
    FAILURES.append(msg)


def warn(msg):
    print(f"  [warn] {msg}")
    WARNINGS.append(msg)


# Columns the dashboard reads, by table.
DASHBOARD_COLUMNS = {
    "seller_features": [
        "seller_id", "total_orders", "cancellation_rate", "late_delivery_rate",
        "average_delivery_days", "average_shipping_delay", "average_review_score",
        "inactive_days", "activity_change", "anomaly_score", "is_anomaly",
        "ghost_listing_score", "behavioral_score", "deterioration_score",
        "risk_score", "risk_level", "possible_ghost_listing",
    ],
    "product_features": [
        "product_id", "seller_id", "category", "total_orders", "cancellation_rate",
        "late_delivery_rate", "average_delivery_days", "average_shipping_delay",
        "ghost_listing_score", "ghost_level", "risk_score", "risk_level",
        "possible_ghost_listing", "activity_change", "inactive_days",
    ],
    "risk_explanations": ["seller_id", "risk_score", "risk_level", "reasons"],
    "product_explanations": ["product_id", "risk_score", "risk_level", "reasons"],
    "daily_trends": [
        "date", "orders", "cancellation_rate", "late_delivery_rate",
        "avg_delivery_days", "avg_shipping_delay",
    ],
    "evaluation_metrics": ["metric", "value"],
    "run_meta": ["key", "value"],
}


def check_imports():
    print("\n1. DEPENDENCIES")
    for name in ("pandas", "numpy", "sklearn", "joblib"):
        try:
            __import__(name)
            ok(f"{name} imports")
        except ImportError as exc:
            fail(f"{name} missing ({exc}) - run: pip install -r requirements.txt")

    for name in ("streamlit", "plotly"):
        try:
            __import__(name)
            ok(f"{name} imports")
        except ImportError:
            warn(f"{name} not installed - the dashboard will not start. "
                 f"Run: pip install -r requirements.txt")


def check_dataset():
    print("\n2. DATASET")
    from src import data_loader
    try:
        source, folder = data_loader.detect_data_source()
        ok(f"dataset found ({source}) at {folder}")
        return True
    except FileNotFoundError as exc:
        fail(str(exc).splitlines()[0])
        return False


def check_database():
    print("\n3. DATABASE + DASHBOARD COLUMNS")
    from src import database

    if not config.DB_PATH.exists():
        fail(f"{config.DB_PATH} does not exist - run: python scripts/run_pipeline.py")
        return None

    data = database.load_all()
    for table, columns in DASHBOARD_COLUMNS.items():
        df = data.get(table, pd.DataFrame())
        if df.empty:
            if table in ("product_explanations", "daily_trends"):
                warn(f"table '{table}' is empty")
            else:
                fail(f"table '{table}' is missing or empty")
            continue
        missing = [c for c in columns if c not in df.columns]
        if missing:
            fail(f"table '{table}' is missing columns used by the dashboard: {missing}")
        else:
            ok(f"table '{table}' has all {len(columns)} expected columns "
               f"({len(df):,} rows)")
    return data


def check_model(data):
    print("\n4. MODEL")
    from src import anomaly_detection

    bundle = anomaly_detection.load_model()
    if bundle is None:
        fail(f"no usable model at {config.MODEL_PATH} - run: python scripts/train_model.py")
        return None
    ok(f"model loaded (trained on {bundle['n_training_rows']} rows)")

    if data is None or data["seller_features"].empty:
        return bundle
    try:
        scored = anomaly_detection.score(data["seller_features"].head(20), bundle)
        span = scored["anomaly_score"].max() - scored["anomaly_score"].min()
        ok(f"model scores fine (anomaly score range on 20 rows: {span:.1f})")
    except Exception as exc:                        # noqa: BLE001
        fail(f"model failed to score: {exc}")
    return bundle


def check_stress(data, bundle):
    print("\n5. STRESS TEST")
    if data is None or data["seller_features"].empty:
        fail("no seller features - cannot run the stress test")
        return
    from src import stress_test
    try:
        summary, detail = stress_test.run_stress_test(data["seller_features"], bundle)
    except Exception as exc:                        # noqa: BLE001
        fail(f"stress test raised: {exc}")
        return

    ok(f"stress test ran: {summary['n_detected']}/{summary['n_degraded']} degraded "
       f"detected, {summary['false_alarms']} false alarms")
    if summary["n_detected"] < summary["n_degraded"]:
        warn("not every degraded seller was detected - consider lowering "
             "HIGH_RISK_THRESHOLD in config.py")


def check_leakage():
    print("\n6. LEAKAGE GUARD")
    from src import database
    data = database.load_all()
    meta = database.read_meta(data.get("run_meta", pd.DataFrame()))
    cutoff = meta.get("cutoff_date")
    if not cutoff:
        warn("no cutoff date recorded in run_meta")
        return
    ok(f"feature/outcome cutoff recorded: {cutoff}")

    detail = data.get("evaluation_detail", pd.DataFrame())
    if detail.empty:
        warn("no evaluation detail stored")
        return
    leaked = [c for c in detail.columns
              if c.startswith("outcome_") and c in DASHBOARD_COLUMNS["seller_features"]]
    if leaked:
        fail(f"outcome columns leaked into the feature table: {leaked}")
    else:
        ok("no outcome-window column appears in the feature table")


def main():
    print("=" * 62)
    print("TRUSTCATALOG SELF-CHECK")
    print("=" * 62)

    check_imports()
    if not check_dataset():
        print("\nRESULT: FAILED - generate data first.")
        sys.exit(1)

    data = check_database()
    bundle = check_model(data)
    if data is not None:
        check_stress(data, bundle)
        check_leakage()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"RESULT: ALL CHECKS PASSED ({len(WARNINGS)} warning(s))")
    for w in WARNINGS:
        print(f"  - {w}")
    print("Safe to demo. Launch with:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
