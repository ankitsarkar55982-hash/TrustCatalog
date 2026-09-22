"""
Cleaning + merging.

Produces two analytical tables:

  order_items_clean : one row per (order_id, order_item_id)  -> product level
  seller_orders     : one row per (seller_id, order_id)      -> seller level

Why two tables?
An order can contain several items from the same seller. If we aggregated the
raw item table per seller we would count that order several times and inflate
every rate. seller_orders de-duplicates on (seller_id, order_id) first.
"""

import numpy as np
import pandas as pd

import config

ORDERS_COLS = [
    "order_id",
    "order_status",
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]

ITEMS_COLS = [
    "order_id",
    "order_item_id",
    "product_id",
    "seller_id",
    "shipping_limit_date",
    "price",
    "freight_value",
]


def _ensure_columns(df, columns):
    """Add any missing column as NaN so downstream code never KeyErrors."""
    for col in columns:
        if col not in df.columns:
            df[col] = np.nan
    return df


def _days_between(later, earlier):
    """Difference in days as float; NaN when either side is missing."""
    delta = later - earlier
    return delta.dt.total_seconds() / 86400.0


def clean_orders(orders):
    orders = orders.copy()
    orders = _ensure_columns(orders, ORDERS_COLS)

    orders = orders.dropna(subset=["order_id"])
    orders = orders.drop_duplicates(subset=["order_id"], keep="first")

    orders["order_status"] = (
        orders["order_status"].astype("object").fillna("unknown")
        .astype(str).str.strip().str.lower()
    )

    for col in ORDERS_COLS:
        if col.endswith(("timestamp", "_at", "_date")):
            orders[col] = pd.to_datetime(orders[col], errors="coerce")

    # An order without a purchase timestamp cannot be placed on the timeline.
    orders = orders.dropna(subset=["order_purchase_timestamp"])

    # Invalid timestamps: delivery before purchase is impossible -> drop value.
    bad = orders["order_delivered_customer_date"] < orders["order_purchase_timestamp"]
    orders.loc[bad.fillna(False), "order_delivered_customer_date"] = pd.NaT

    bad = orders["order_delivered_carrier_date"] < orders["order_purchase_timestamp"]
    orders.loc[bad.fillna(False), "order_delivered_carrier_date"] = pd.NaT

    return orders


def clean_items(items):
    items = items.copy()
    items = _ensure_columns(items, ITEMS_COLS)

    items = items.dropna(subset=["order_id", "seller_id", "product_id"])
    items = items.drop_duplicates(subset=["order_id", "order_item_id"], keep="first")

    items["shipping_limit_date"] = pd.to_datetime(
        items["shipping_limit_date"], errors="coerce"
    )
    for col in ("price", "freight_value"):
        items[col] = pd.to_numeric(items[col], errors="coerce")

    # Negative money is always a data error.
    items.loc[items["price"] < 0, "price"] = np.nan
    items.loc[items["freight_value"] < 0, "freight_value"] = np.nan

    return items


def clean_reviews(reviews):
    if reviews is None or reviews.empty:
        return None
    reviews = reviews.copy()
    if "order_id" not in reviews.columns or "review_score" not in reviews.columns:
        return None

    reviews["review_score"] = pd.to_numeric(reviews["review_score"], errors="coerce")
    reviews = reviews.dropna(subset=["order_id", "review_score"])
    reviews = reviews[reviews["review_score"].between(1, 5)]

    # Keep one review per order (the latest one when there are duplicates).
    if "review_creation_date" in reviews.columns:
        reviews["review_creation_date"] = pd.to_datetime(
            reviews["review_creation_date"], errors="coerce"
        )
        reviews = reviews.sort_values("review_creation_date")
    reviews = reviews.drop_duplicates(subset=["order_id"], keep="last")

    return reviews[["order_id", "review_score"]]


def build_analytical_tables(tables):
    """
    Merge everything into the two analytical tables described at the top.
    """
    orders = clean_orders(tables["orders"])
    items = clean_items(tables["order_items"])

    if orders.empty or items.empty:
        raise ValueError(
            "After cleaning, orders or order_items is empty - nothing to analyse."
        )

    merged = items.merge(orders, on="order_id", how="inner")
    if merged.empty:
        raise ValueError(
            "orders and order_items share no order_id values. "
            "Check that both CSV files come from the same dataset."
        )

    # ---------------- derived per-item timing columns ----------------
    merged["delivery_days"] = _days_between(
        merged["order_delivered_customer_date"], merged["order_purchase_timestamp"]
    )
    # Seller-controlled segment: approval -> handover to the carrier.
    merged["shipping_delay"] = _days_between(
        merged["order_delivered_carrier_date"], merged["order_approved_at"]
    )
    merged.loc[merged["shipping_delay"] < 0, "shipping_delay"] = 0.0

    merged["delivery_lateness"] = _days_between(
        merged["order_delivered_customer_date"], merged["order_estimated_delivery_date"]
    )

    merged["is_failed"] = merged["order_status"].isin(config.FAILED_STATUSES)
    merged["is_cancelled"] = merged["is_failed"]
    merged["is_unavailable"] = merged["order_status"].eq("unavailable")
    merged["is_delivered"] = merged["order_status"].eq("delivered")
    merged["is_late"] = (merged["delivery_lateness"] > 0).fillna(False)

    # Seller promised to hand the parcel over before shipping_limit_date.
    merged["shipping_limit_breach"] = (
        merged["order_delivered_carrier_date"] > merged["shipping_limit_date"]
    ).fillna(False)

    # An order that never reached the carrier but is not delivered either.
    merged["never_shipped"] = (
        merged["order_delivered_carrier_date"].isna() & ~merged["is_delivered"]
    )

    # ---------------- attach reviews (optional) ----------------
    reviews = clean_reviews(tables.get("reviews"))
    if reviews is not None:
        merged = merged.merge(reviews, on="order_id", how="left")
    else:
        merged["review_score"] = np.nan

    # ---------------- attach product category (optional) ----------------
    products = tables.get("products")
    if products is not None and "product_id" in products.columns:
        cols = ["product_id"]
        if "product_category_name" in products.columns:
            cols.append("product_category_name")
        prod = products[cols].drop_duplicates(subset=["product_id"])
        merged = merged.merge(prod, on="product_id", how="left")

    if "product_category_name" not in merged.columns:
        merged["product_category_name"] = "unknown"
    merged["product_category_name"] = (
        merged["product_category_name"].astype("object").fillna("unknown")
    )

    translation = tables.get("category_translation")
    if (
        translation is not None
        and "product_category_name" in translation.columns
        and "product_category_name_english" in translation.columns
    ):
        translation = translation.drop_duplicates(subset=["product_category_name"])
        merged = merged.merge(translation, on="product_category_name", how="left")
        merged["category"] = (
            merged["product_category_name_english"]
            .astype("object")
            .fillna(merged["product_category_name"])
        )
    else:
        merged["category"] = merged["product_category_name"]

    # ---------------- seller x order table ----------------
    agg_map = {
        "order_status": "first",
        "order_purchase_timestamp": "first",
        "order_approved_at": "first",
        "order_delivered_carrier_date": "first",
        "order_delivered_customer_date": "first",
        "order_estimated_delivery_date": "first",
        "delivery_days": "mean",
        "shipping_delay": "mean",
        "delivery_lateness": "mean",
        "is_failed": "max",
        "is_unavailable": "max",
        "is_delivered": "max",
        "is_late": "max",
        "shipping_limit_breach": "max",
        "never_shipped": "max",
        "review_score": "mean",
        "product_id": "nunique",
        "price": "sum",
    }
    seller_orders = (
        merged.groupby(["seller_id", "order_id"], as_index=False, observed=True)
        .agg(agg_map)
        .rename(columns={"product_id": "n_products_in_order"})
    )
    seller_orders["is_cancelled"] = seller_orders["is_failed"]

    return merged, seller_orders


def split_windows(df, timestamp_col="order_purchase_timestamp"):
    """
    Split a table into FEATURE WINDOW and OUTCOME WINDOW at a cutoff date.

    The cutoff is the FEATURE_WINDOW_FRACTION quantile of the purchase
    timestamps, so the split adapts to whatever date range the data covers.
    Features are built ONLY from the feature window; labels ONLY from the
    outcome window. That is what prevents leakage.
    """
    ts = pd.to_datetime(df[timestamp_col], errors="coerce").dropna()
    if ts.empty:
        raise ValueError("No valid purchase timestamps - cannot split windows.")

    cutoff = ts.quantile(config.FEATURE_WINDOW_FRACTION)
    cutoff = pd.Timestamp(cutoff).normalize()

    feature_df = df[df[timestamp_col] < cutoff].copy()
    outcome_df = df[df[timestamp_col] >= cutoff].copy()

    # Degenerate case (all orders on one day): fall back to a 50/50 row split.
    if feature_df.empty or outcome_df.empty:
        df_sorted = df.sort_values(timestamp_col)
        half = max(1, len(df_sorted) // 2)
        feature_df = df_sorted.iloc[:half].copy()
        outcome_df = df_sorted.iloc[half:].copy()
        cutoff = pd.Timestamp(df_sorted[timestamp_col].iloc[half - 1])

    return feature_df, outcome_df, cutoff
