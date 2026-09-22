"""
TrustCatalog end-to-end pipeline.

Run:  python scripts/run_pipeline.py
      python scripts/run_pipeline.py --no-train     (reuse the saved model)

Order of operations (and why):
  1. load + clean
  2. split the timeline into FEATURE WINDOW / OUTCOME WINDOW
  3. build features from the FEATURE WINDOW only          <- no leakage
  4. rule-based behavioural score
  5. train Isolation Forest on the "normal" sellers, then score everyone
  6. ghost score -> final risk score -> explanations
  7. build labels from the OUTCOME WINDOW only and evaluate
  8. write everything to SQLite
"""

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import config
from src import (
    anomaly_detection,
    data_loader,
    database,
    evaluation,
    explanations as explain_mod,
    feature_engineering as fe,
    ghost_listing,
    preprocessing,
    risk_scoring,
)


def _banner(text):
    print("\n" + "=" * 62)
    print(text)
    print("=" * 62)


def build_daily_trends(item_df):
    """Daily aggregates used by the dashboard trend charts."""
    if item_df.empty:
        return pd.DataFrame()
    df = item_df.copy()
    df["date"] = df["order_purchase_timestamp"].dt.floor("D")
    trends = df.groupby("date", as_index=False).agg(
        orders=("order_id", "nunique"),
        cancellation_rate=("is_cancelled", "mean"),
        late_delivery_rate=("is_late", "mean"),
        avg_delivery_days=("delivery_days", "mean"),
        avg_shipping_delay=("shipping_delay", "mean"),
    )
    return trends


def run(train=True, verbose=True):
    started = datetime.now()

    # ---------------------------------------------------------------- 1
    _banner("1/8  LOADING DATA")
    tables, source = data_loader.load_raw_tables(verbose=verbose)

    # ---------------------------------------------------------------- 2
    _banner("2/8  CLEANING + MERGING")
    item_df, seller_orders = preprocessing.build_analytical_tables(tables)
    print(f"  order-item rows : {len(item_df):,}")
    print(f"  seller-order rows: {len(seller_orders):,}")
    print(f"  distinct sellers : {seller_orders['seller_id'].nunique():,}")

    # ---------------------------------------------------------------- 3
    _banner("3/8  TEMPORAL SPLIT (anti-leakage)")
    fw_orders, ow_orders, cutoff = preprocessing.split_windows(seller_orders)
    fw_items = item_df[item_df["order_purchase_timestamp"] < cutoff].copy()

    window_start = pd.Timestamp(seller_orders["order_purchase_timestamp"].min())
    print(f"  feature window : {window_start.date()}  ->  {cutoff.date()}  "
          f"({len(fw_orders):,} seller-orders)")
    print(f"  outcome window : {cutoff.date()}  ->  "
          f"{pd.Timestamp(seller_orders['order_purchase_timestamp'].max()).date()}  "
          f"({len(ow_orders):,} seller-orders)")

    if fw_orders.empty:
        raise ValueError("The feature window is empty - the dataset is too short.")

    # ---------------------------------------------------------------- 4
    _banner("4/8  FEATURE ENGINEERING")
    seller_features = fe.build_seller_features(fw_orders, cutoff, window_start)
    seller_features = fe.enrich_with_product_counts(seller_features, fw_items)

    before = len(seller_features)
    seller_features = seller_features[
        seller_features["total_orders"] >= config.MIN_ORDERS_FOR_SCORING
    ].reset_index(drop=True)
    print(f"  sellers with features : {before:,}")
    print(f"  scored (>= {config.MIN_ORDERS_FOR_SCORING} orders): {len(seller_features):,}")

    product_features = fe.build_product_features(fw_items, cutoff, window_start)
    print(f"  listings with features: {len(product_features):,}")

    if seller_features.empty:
        raise ValueError(
            "No seller has enough orders in the feature window. "
            "Lower MIN_ORDERS_FOR_SCORING in config.py or use a larger dataset."
        )

    # ---------------------------------------------------------------- 5
    _banner("5/8  ANOMALY DETECTION (Isolation Forest)")
    behavioral = risk_scoring.compute_behavioral_score(seller_features)

    bundle = None
    if train:
        bundle = anomaly_detection.train_model(seller_features, behavioral)
        path = anomaly_detection.save_model(bundle)
        print(f"  model saved to {path}")
    else:
        bundle = anomaly_detection.load_model()
        if bundle is None:
            print("  [warn] no saved model found - training a new one.")
            bundle = anomaly_detection.train_model(seller_features, behavioral)
            anomaly_detection.save_model(bundle)

    seller_features = anomaly_detection.score(seller_features, bundle)
    print(f"  anomalies flagged: {int(seller_features['is_anomaly'].sum()):,}")

    # Listings reuse the same model (same feature names).
    if not product_features.empty:
        product_features = anomaly_detection.score(product_features, bundle)

    # ---------------------------------------------------------------- 6
    _banner("6/8  GHOST + RISK SCORING")
    seller_features = ghost_listing.add_ghost_scores(seller_features)
    seller_features = risk_scoring.compute_risk(seller_features)

    if not product_features.empty:
        product_features = ghost_listing.add_ghost_scores(product_features)
        # A listing inherits part of its seller's risk.
        seller_risk = seller_features.set_index("seller_id")["risk_score"]
        product_features["seller_risk"] = (
            product_features["seller_id"].map(seller_risk).fillna(0.0)
        )
        product_features = risk_scoring.compute_risk(product_features)
        # Blend: 75% own behaviour, 25% seller-level context.
        product_features["risk_score"] = (
            0.75 * product_features["risk_score"]
            + 0.25 * product_features["seller_risk"]
        ).clip(0, 100).round(2)
        product_features["risk_level"] = product_features["risk_score"].map(
            risk_scoring.risk_level
        )

    counts = seller_features["risk_level"].value_counts()
    for level in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        print(f"  {level:<9}: {int(counts.get(level, 0)):>5}")
    print(f"  possible ghost listings (sellers): "
          f"{int(seller_features['possible_ghost_listing'].sum()):,}")
    if not product_features.empty:
        print(f"  possible ghost listings (products): "
              f"{int(product_features['possible_ghost_listing'].sum()):,}")

    # ---------------------------------------------------------------- 7
    _banner("7/8  EVALUATION (outcome window)")
    labels = fe.build_outcome_labels(ow_orders)
    metrics, merged = evaluation.evaluate(seller_features, labels)

    for key in ("n_evaluated", "n_positives", "base_rate",
                "precision_at_5", "precision_at_10", "precision_at_20",
                "precision", "recall", "f1", "lift_at_10"):
        value = metrics.get(key)
        if isinstance(value, float):
            print(f"  {key:<16}: {value:.3f}" if value == value else f"  {key:<16}: n/a")
        else:
            print(f"  {key:<16}: {value}")

    seller_explanations = explain_mod.generate_explanations(
        seller_features, id_column="seller_id"
    )
    product_explanations = (
        explain_mod.generate_explanations(product_features, id_column="product_id")
        if not product_features.empty else pd.DataFrame()
    )

    # ---------------------------------------------------------------- 8
    _banner("8/8  SAVING RESULTS")
    meta = {
        "data_source": source,
        "cutoff_date": str(cutoff),
        "window_start": str(window_start),
        "window_end": str(pd.Timestamp(seller_orders["order_purchase_timestamp"].max())),
        "n_orders": int(seller_orders["order_id"].nunique()),
        "n_sellers": int(seller_orders["seller_id"].nunique()),
        "n_products": int(item_df["product_id"].nunique()),
        "n_scored_sellers": int(len(seller_features)),
        "n_scored_listings": int(len(product_features)),
        "risk_weights": config.RISK_WEIGHTS,
        "run_at": started.isoformat(timespec="seconds"),
        "runtime_seconds": round((datetime.now() - started).total_seconds(), 2),
    }

    database.save_pipeline_results(
        seller_features=seller_features,
        product_features=product_features,
        explanations=seller_explanations,
        product_explanations=product_explanations,
        evaluation_metrics=metrics,
        meta=meta,
    )

    trends = build_daily_trends(item_df)
    conn = database.get_connection()
    try:
        database.write_table(conn, "daily_trends", trends)
        database.write_table(conn, "evaluation_detail", merged)
    finally:
        conn.close()

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    seller_features.to_csv(config.PROCESSED_DIR / "seller_features.csv", index=False)
    if not product_features.empty:
        product_features.to_csv(config.PROCESSED_DIR / "product_features.csv", index=False)

    print(f"  database : {config.DB_PATH}")
    print(f"  processed CSVs: {config.PROCESSED_DIR}")
    print(f"\nDone in {meta['runtime_seconds']}s.")
    print("Next step:  streamlit run dashboard/app.py")

    return seller_features, product_features, metrics


def main():
    parser = argparse.ArgumentParser(description="Run the TrustCatalog pipeline.")
    parser.add_argument(
        "--no-train", action="store_true",
        help="Reuse the saved Isolation Forest instead of retraining it.",
    )
    args = parser.parse_args()

    try:
        run(train=not args.no_train)
    except FileNotFoundError as exc:
        print("\nDATA NOT FOUND\n")
        print(exc)
        print("\nFix: python scripts/generate_demo_data.py")
        sys.exit(1)
    except Exception:                               # noqa: BLE001
        print("\nPIPELINE FAILED\n")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
