"""
Train (or retrain) only the Isolation Forest and save it to models/.

Run:  python scripts/train_model.py

Most of the time you do not need this - run_pipeline.py trains the model as
step 5. This script exists so the model can be refreshed on its own.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import config
from src import (
    anomaly_detection,
    data_loader,
    feature_engineering as fe,
    preprocessing,
    risk_scoring,
)


def main():
    print("Loading data...")
    tables, source = data_loader.load_raw_tables(verbose=True)

    item_df, seller_orders = preprocessing.build_analytical_tables(tables)
    fw_orders, _, cutoff = preprocessing.split_windows(seller_orders)
    window_start = pd.Timestamp(seller_orders["order_purchase_timestamp"].min())

    features = fe.build_seller_features(fw_orders, cutoff, window_start)
    features = features[features["total_orders"] >= config.MIN_ORDERS_FOR_SCORING]

    if features.empty:
        print("No sellers to train on. Run scripts/generate_demo_data.py first.")
        sys.exit(1)

    behavioral = risk_scoring.compute_behavioral_score(features)
    bundle = anomaly_detection.train_model(features, behavioral)
    path = anomaly_detection.save_model(bundle)

    print(f"Model saved to {path}")
    print(f"  data source     : {source}")
    print(f"  training rows   : {bundle['n_training_rows']}")
    print(f"  features used   : {', '.join(bundle['feature_names'])}")


if __name__ == "__main__":
    main()
