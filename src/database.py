"""
SQLite persistence.

The pipeline writes results here once; the dashboard only reads. That is why
the dashboard never retrains a model and starts instantly.

Every read is defensive: if the database or a table is missing, an empty
DataFrame comes back and the dashboard shows a friendly message instead of a
traceback.
"""

import json
import sqlite3
from datetime import datetime

import pandas as pd

import config

TABLES = [
    "sellers",
    "products",
    "seller_features",
    "product_features",
    "risk_predictions",
    "risk_explanations",
    "product_explanations",
    "stress_test_results",
    "stress_test_detail",
    "evaluation_metrics",
    "evaluation_detail",
    "daily_trends",
    "run_meta",
]


def get_connection(path=None):
    """Open a connection, creating the folder if needed."""
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _sanitise(df):
    """
    Make a DataFrame safe for sqlite3: datetimes -> ISO strings, bools -> int,
    and any leftover object column -> str. sqlite3 cannot bind Timestamp or
    numpy.bool_ objects.
    """
    out = df.copy()
    for col in out.columns:
        series = out[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            out[col] = series.astype("object").where(series.notna(), None)
            out[col] = out[col].map(lambda v: v.isoformat() if hasattr(v, "isoformat") else v)
        elif pd.api.types.is_bool_dtype(series):
            out[col] = series.astype(int)
        elif series.dtype == "object":
            out[col] = series.map(lambda v: v if v is None or isinstance(v, (str, int, float)) else str(v))
    return out


def write_table(conn, name, df):
    """Replace a table wholesale. Empty frames still create the table."""
    if df is None:
        df = pd.DataFrame()
    _sanitise(df).to_sql(name, conn, if_exists="replace", index=False)
    conn.commit()


def table_exists(conn, name):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def read_table(conn, name):
    """Return the table, or an empty DataFrame when it does not exist."""
    try:
        if not table_exists(conn, name):
            return pd.DataFrame()
        return pd.read_sql_query(f"SELECT * FROM '{name}'", conn)
    except Exception as exc:                        # noqa: BLE001
        print(f"  [warn] could not read table '{name}': {exc}")
        return pd.DataFrame()


def load_all(path=None):
    """
    Read every table in one go. Returns a dict of DataFrames; missing tables
    become empty DataFrames rather than KeyErrors.
    """
    path = path or config.DB_PATH
    if not path.exists():
        return {name: pd.DataFrame() for name in TABLES}

    conn = get_connection(path)
    try:
        return {name: read_table(conn, name) for name in TABLES}
    finally:
        conn.close()


def save_pipeline_results(
    seller_features,
    product_features,
    explanations,
    product_explanations,
    evaluation_metrics,
    meta,
    path=None,
):
    """Persist one complete pipeline run."""
    conn = get_connection(path)
    try:
        write_table(conn, "seller_features", seller_features)
        write_table(conn, "product_features", product_features)
        write_table(conn, "risk_explanations", explanations)
        write_table(conn, "product_explanations", product_explanations)

        # sellers / products dimension tables
        if not seller_features.empty:
            write_table(conn, "sellers", seller_features[["seller_id"]].drop_duplicates())
        if not product_features.empty:
            cols = [c for c in ("product_id", "seller_id", "category")
                    if c in product_features.columns]
            write_table(conn, "products", product_features[cols].drop_duplicates())

        # flat prediction table
        pred_cols = [
            c for c in (
                "seller_id", "behavioral_score", "anomaly_score", "is_anomaly",
                "ghost_listing_score", "deterioration_score", "risk_score",
                "risk_level", "possible_ghost_listing",
            ) if c in seller_features.columns
        ]
        write_table(
            conn, "risk_predictions",
            seller_features[pred_cols] if pred_cols else pd.DataFrame(),
        )

        metrics_df = pd.DataFrame(
            [{"metric": k, "value": json.dumps(v, default=str)}
             for k, v in (evaluation_metrics or {}).items()]
        )
        write_table(conn, "evaluation_metrics", metrics_df)

        meta_df = pd.DataFrame(
            [{"key": k, "value": json.dumps(v, default=str)}
             for k, v in (meta or {}).items()]
        )
        write_table(conn, "run_meta", meta_df)
    finally:
        conn.close()


def save_stress_test(summary, detail, path=None):
    """Store the most recent stress-test run so the dashboard can show it."""
    conn = get_connection(path)
    try:
        record = dict(summary)
        record["run_at"] = datetime.now().isoformat(timespec="seconds")
        write_table(conn, "stress_test_results", pd.DataFrame([record]))
        write_table(conn, "stress_test_detail", detail)
    finally:
        conn.close()


def read_meta(meta_df):
    """Turn the run_meta key/value table back into a plain dict."""
    if meta_df is None or meta_df.empty or "key" not in meta_df.columns:
        return {}
    out = {}
    for _, row in meta_df.iterrows():
        try:
            out[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            out[row["key"]] = row["value"]
    return out
