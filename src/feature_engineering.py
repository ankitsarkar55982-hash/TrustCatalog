"""
Behavioural feature engineering.

Everything here is computed from the FEATURE WINDOW only. The cutoff date is
passed in explicitly and is used as "today" - so no future information is ever
visible to the features.
"""

import numpy as np
import pandas as pd

import config


def _safe_div(numerator, denominator):
    """Element-wise division that returns 0 instead of inf/NaN."""
    num = pd.Series(numerator, dtype="float64")
    den = pd.Series(denominator, dtype="float64").replace(0, np.nan)
    return (num / den).fillna(0.0)


def _rate(series):
    """Mean of a boolean-ish series, 0 when empty."""
    if len(series) == 0:
        return 0.0
    return float(pd.Series(series).astype(float).mean())


def build_seller_features(seller_orders, cutoff, window_start=None):
    """
    One row per seller, built only from rows already filtered to the
    feature window.
    """
    if seller_orders.empty:
        return pd.DataFrame()

    df = seller_orders.copy()
    cutoff = pd.Timestamp(cutoff)
    if window_start is None:
        window_start = pd.Timestamp(df["order_purchase_timestamp"].min())
    window_days = max((cutoff - window_start).days, 1)

    recent_start = cutoff - pd.Timedelta(days=config.RECENT_DAYS)
    short_start = cutoff - pd.Timedelta(days=config.SHORT_RECENT_DAYS)

    df["_recent"] = df["order_purchase_timestamp"] >= recent_start
    df["_short"] = df["order_purchase_timestamp"] >= short_start

    rows = []
    for seller_id, g in df.groupby("seller_id", observed=True):
        total_orders = len(g)
        delivered = g[g["is_delivered"]]
        recent = g[g["_recent"]]
        older = g[~g["_recent"]]

        cancelled_orders = int(g["is_cancelled"].sum())
        late_orders = int(g["is_late"].sum())

        # --- activity ---
        last_order = g["order_purchase_timestamp"].max()
        inactive_days = float(max((cutoff - last_order).days, 0))

        recent_rate = len(recent) / float(config.RECENT_DAYS)
        older_days = max(window_days - config.RECENT_DAYS, 1)
        baseline_rate = len(older) / float(older_days)

        if baseline_rate > 0:
            activity_change = (recent_rate - baseline_rate) / baseline_rate
        else:
            activity_change = 1.0 if recent_rate > 0 else 0.0
        activity_change = float(np.clip(activity_change, -1.0, 3.0))

        # --- timing ---
        delivery_days = delivered["delivery_days"].dropna()
        shipping_delay = g["shipping_delay"].dropna()
        reviews = g["review_score"].dropna()

        rows.append(
            {
                "seller_id": seller_id,
                "total_orders": total_orders,
                "cancelled_orders": cancelled_orders,
                "cancellation_rate": _rate(g["is_cancelled"]),
                "delivered_orders": int(len(delivered)),
                "late_orders": late_orders,
                "late_delivery_rate": _rate(delivered["is_late"]) if len(delivered) else 0.0,
                "average_delivery_days": float(delivery_days.mean()) if len(delivery_days) else 0.0,
                "average_shipping_delay": float(shipping_delay.mean()) if len(shipping_delay) else 0.0,
                "average_review_score": float(reviews.mean()) if len(reviews) else 5.0,
                "total_products": int(g["n_products_in_order"].sum()),
                "unique_products": int(g["n_products_in_order"].max()) if total_orders else 0,
                "orders_per_day": total_orders / float(window_days),
                "orders_last_7_days": int(g["_short"].sum()),
                "orders_last_30_days": int(g["_recent"].sum()),
                "activity_change": activity_change,
                "delivery_time_variance": float(delivery_days.var(ddof=0)) if len(delivery_days) > 1 else 0.0,
                "shipping_delay_variance": float(shipping_delay.var(ddof=0)) if len(shipping_delay) > 1 else 0.0,
                "failed_order_rate": _rate(g["is_failed"]),
                "recent_cancellation_rate": _rate(recent["is_cancelled"]) if len(recent) else 0.0,
                "recent_late_delivery_rate": (
                    _rate(recent[recent["is_delivered"]]["is_late"])
                    if len(recent[recent["is_delivered"]]) else 0.0
                ),
                "recent_shipping_delay": (
                    float(recent["shipping_delay"].dropna().mean())
                    if len(recent["shipping_delay"].dropna()) else 0.0
                ),
                "inactive_days": inactive_days,
                "unavailable_rate": _rate(g["is_unavailable"]),
                "never_shipped_rate": _rate(g["never_shipped"]),
                "shipping_limit_breach_rate": _rate(g["shipping_limit_breach"]),
                "revenue": float(g["price"].fillna(0).sum()),
            }
        )

    features = pd.DataFrame(rows)
    if features.empty:
        return features

    # True distinct-product count needs the item table; approximate safely here
    # and overwrite in enrich_with_product_counts() when item data is present.
    features["unique_products"] = features["unique_products"].clip(lower=1)

    features = features.sort_values("seller_id").reset_index(drop=True)
    return _finalise(features)


def enrich_with_product_counts(seller_features, item_df):
    """Replace the approximate product counts with exact ones."""
    if seller_features.empty or item_df.empty:
        return seller_features
    counts = (
        item_df.groupby("seller_id", observed=True)["product_id"]
        .nunique()
        .rename("unique_products")
        .reset_index()
    )
    out = seller_features.drop(columns=["unique_products"]).merge(
        counts, on="seller_id", how="left"
    )
    out["unique_products"] = out["unique_products"].fillna(1).astype(int)
    return out


def build_product_features(item_df, cutoff, window_start=None):
    """One row per (product_id, seller_id) listing."""
    if item_df.empty:
        return pd.DataFrame()

    df = item_df.copy()
    cutoff = pd.Timestamp(cutoff)
    if window_start is None:
        window_start = pd.Timestamp(df["order_purchase_timestamp"].min())
    window_days = max((cutoff - window_start).days, 1)

    recent_start = cutoff - pd.Timedelta(days=config.RECENT_DAYS)
    df["_recent"] = df["order_purchase_timestamp"] >= recent_start

    # One row per listing x order, so multi-item orders do not double count.
    df = df.drop_duplicates(subset=["product_id", "seller_id", "order_id"])

    rows = []
    for (product_id, seller_id), g in df.groupby(
        ["product_id", "seller_id"], observed=True
    ):
        delivered = g[g["is_delivered"]]
        delivery_days = delivered["delivery_days"].dropna()
        shipping_delay = g["shipping_delay"].dropna()
        reviews = g["review_score"].dropna()
        recent = g[g["_recent"]]
        older = g[~g["_recent"]]

        recent_rate = len(recent) / float(config.RECENT_DAYS)
        older_days = max(window_days - config.RECENT_DAYS, 1)
        baseline_rate = len(older) / float(older_days)
        if baseline_rate > 0:
            activity_change = (recent_rate - baseline_rate) / baseline_rate
        else:
            activity_change = 1.0 if recent_rate > 0 else 0.0

        last_order = g["order_purchase_timestamp"].max()

        rows.append(
            {
                "product_id": product_id,
                "seller_id": seller_id,
                "category": g["category"].iloc[0] if "category" in g.columns else "unknown",
                "total_orders": int(len(g)),
                "cancellation_rate": _rate(g["is_cancelled"]),
                "late_delivery_rate": _rate(delivered["is_late"]) if len(delivered) else 0.0,
                "average_delivery_days": float(delivery_days.mean()) if len(delivery_days) else 0.0,
                "average_shipping_delay": float(shipping_delay.mean()) if len(shipping_delay) else 0.0,
                "average_review_score": float(reviews.mean()) if len(reviews) else 5.0,
                "shipping_delay": float(shipping_delay.mean()) if len(shipping_delay) else 0.0,
                "recent_activity": int(len(recent)),
                "activity_change": float(np.clip(activity_change, -1.0, 3.0)),
                "inactive_days": float(max((cutoff - last_order).days, 0)),
                "unavailable_rate": _rate(g["is_unavailable"]),
                "never_shipped_rate": _rate(g["never_shipped"]),
                "shipping_limit_breach_rate": _rate(g["shipping_limit_breach"]),
                "delivery_time_variance": float(delivery_days.var(ddof=0)) if len(delivery_days) > 1 else 0.0,
                "shipping_delay_variance": float(shipping_delay.var(ddof=0)) if len(shipping_delay) > 1 else 0.0,
                "recent_cancellation_rate": _rate(recent["is_cancelled"]) if len(recent) else 0.0,
                "recent_late_delivery_rate": (
                    _rate(recent[recent["is_delivered"]]["is_late"])
                    if len(recent[recent["is_delivered"]]) else 0.0
                ),
                "orders_per_day": len(g) / float(window_days),
            }
        )

    features = pd.DataFrame(rows)
    if features.empty:
        return features
    return _finalise(features.sort_values(["seller_id", "product_id"]).reset_index(drop=True))


def _finalise(features):
    """Replace inf/NaN and force numeric dtypes - protects sklearn later."""
    numeric_cols = features.select_dtypes(include=[np.number]).columns
    features[numeric_cols] = (
        features[numeric_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    )
    return features


def build_outcome_labels(outcome_orders):
    """
    Ground-truth-ish labels computed ONLY from the outcome window.

    A seller is labelled 1 ("actually had trouble") when their failure rate in
    the outcome window exceeds the configured threshold and they had enough
    orders for the rate to mean anything.
    """
    if outcome_orders.empty:
        return pd.DataFrame(
            columns=["seller_id", "outcome_orders", "outcome_failures",
                     "outcome_failure_rate", "label"]
        )

    df = outcome_orders.copy()
    df["_bad"] = df["is_failed"] | df["is_late"]

    grouped = df.groupby("seller_id", observed=True).agg(
        outcome_orders=("order_id", "count"),
        outcome_failures=("_bad", "sum"),
    ).reset_index()

    grouped["outcome_failures"] = grouped["outcome_failures"].astype(int)
    grouped["outcome_failure_rate"] = _safe_div(
        grouped["outcome_failures"], grouped["outcome_orders"]
    )
    grouped["label"] = (
        (grouped["outcome_failure_rate"] >= config.OUTCOME_FAILURE_RATE_THRESHOLD)
        & (grouped["outcome_orders"] >= config.MIN_OUTCOME_ORDERS)
    ).astype(int)

    return grouped
