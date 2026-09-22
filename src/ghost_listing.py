"""
"Possible Ghost Listing" scoring.

A ghost listing is a listing that still looks orderable but whose seller can no
longer fulfil it. There is no single tell-tale sign, so the score combines six
independent behavioural signals, each normalised to 0-1 and weighted.

Nothing here claims fraud. The output is a *concern level*.
"""

import numpy as np
import pandas as pd

import config


def _norm(series, cap):
    """Scale a non-negative series to 0-1, saturating at `cap`."""
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if cap <= 0:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s / float(cap)).clip(0.0, 1.0)


def _col(df, name, default=0.0):
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default)
    return pd.Series(np.full(len(df), default, dtype="float64"), index=df.index)


def compute_ghost_score(features, weights=None):
    """
    Returns a Series of ghost scores (0-100) aligned to `features.index`.
    Works for both seller-level and product-level tables.
    """
    if features is None or features.empty:
        return pd.Series(dtype="float64")

    w = dict(config.GHOST_WEIGHTS)
    if weights:
        w.update(weights)

    signals = {}

    # 1. The listing has gone quiet.
    signals["inactivity"] = _norm(
        _col(features, "inactive_days"), config.GHOST_INACTIVE_DAYS_CAP
    )

    # 2. Order volume collapsed relative to its own history
    #    (activity_change is negative when volume drops).
    activity_change = _col(features, "activity_change")
    signals["activity_drop"] = (-activity_change).clip(0.0, 1.0)

    # 3. Orders arrive and then get cancelled.
    signals["cancellation"] = _norm(
        _col(features, "cancellation_rate"),
        config.BEHAVIOR_CAPS["cancellation_rate"],
    )

    # 4. Explicit "unavailable" outcomes, or orders that never reached a carrier.
    unavailable = _norm(_col(features, "unavailable_rate"), 0.20)
    never_shipped = _norm(_col(features, "never_shipped_rate"), 0.30)
    signals["unavailable"] = pd.concat([unavailable, never_shipped], axis=1).max(axis=1)

    # 5. Seller sits on the parcel before handing it over.
    signals["shipping_delay"] = _norm(
        _col(features, "average_shipping_delay"),
        config.BEHAVIOR_CAPS["shipping_delay"],
    )

    # 6. Deliveries that do arrive, arrive late.
    signals["late_delivery"] = _norm(
        _col(features, "late_delivery_rate"),
        config.BEHAVIOR_CAPS["late_delivery_rate"],
    )

    total_weight = sum(w.get(k, 0.0) for k in signals)
    if total_weight <= 0:
        return pd.Series(np.zeros(len(features)), index=features.index)

    score = sum(signals[k] * w.get(k, 0.0) for k in signals) / total_weight
    return (score * 100.0).clip(0.0, 100.0).astype("float64")


def ghost_level(score):
    """Map a numeric ghost score to its configured band label."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "Low concern"
    for low, high, label in config.GHOST_BANDS:
        if low <= value <= high:
            return label
    return config.GHOST_BANDS[-1][2]


def add_ghost_scores(features):
    """Attach ghost_listing_score / ghost_level / possible_ghost_listing."""
    out = features.copy()
    if out.empty:
        out["ghost_listing_score"] = []
        out["ghost_level"] = []
        out["possible_ghost_listing"] = []
        return out

    out["ghost_listing_score"] = compute_ghost_score(out).round(2)
    out["ghost_level"] = out["ghost_listing_score"].map(ghost_level)
    out["possible_ghost_listing"] = (
        out["ghost_listing_score"] >= config.GHOST_FLAG_THRESHOLD
    ).astype(int)
    return out
