"""
Final risk score.

    risk = 0.40 * behavioural_risk      (transparent rules)
         + 0.30 * anomaly_score          (Isolation Forest)
         + 0.20 * ghost_listing_score    (ghost signals)
         + 0.10 * deterioration_score    (recent vs historical)

All four components are on the same 0-100 scale, and all weights live in
config.RISK_WEIGHTS so they can be changed without touching this file.
"""

import numpy as np
import pandas as pd

import config


def _col(df, name, default=0.0):
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default)
    return pd.Series(np.full(len(df), default, dtype="float64"), index=df.index)


def _norm(series, cap):
    if cap <= 0:
        return series.clip(0.0, 1.0)
    return (series / float(cap)).clip(0.0, 1.0)


def compute_behavioral_score(features, weights=None, caps=None):
    """Transparent rule-based component (0-100)."""
    if features is None or features.empty:
        return pd.Series(dtype="float64")

    w = dict(config.BEHAVIOR_WEIGHTS)
    if weights:
        w.update(weights)
    caps = {**config.BEHAVIOR_CAPS, **(caps or {})}

    parts = {}
    parts["cancellation_rate"] = _norm(
        _col(features, "cancellation_rate"), caps["cancellation_rate"]
    )
    parts["late_delivery_rate"] = _norm(
        _col(features, "late_delivery_rate"), caps["late_delivery_rate"]
    )
    parts["shipping_delay"] = _norm(
        _col(features, "average_shipping_delay"), caps["shipping_delay"]
    )
    parts["delivery_days"] = _norm(
        _col(features, "average_delivery_days"), caps["delivery_days"]
    )
    # Reviews: 5 stars -> 0 risk, 1 star -> full risk.
    reviews = _col(features, "average_review_score", 5.0).clip(1.0, 5.0)
    parts["review_score"] = ((5.0 - reviews) / 4.0).clip(0.0, 1.0)
    parts["shipping_limit_breach_rate"] = _norm(
        _col(features, "shipping_limit_breach_rate"),
        caps["shipping_limit_breach_rate"],
    )

    total_weight = sum(w.get(k, 0.0) for k in parts)
    if total_weight <= 0:
        return pd.Series(np.zeros(len(features)), index=features.index)

    score = sum(parts[k] * w.get(k, 0.0) for k in parts) / total_weight
    return (score * 100.0).clip(0.0, 100.0)


def compute_deterioration_score(features):
    """
    How much worse is the seller *recently* compared with their own history?
    This is the early-warning component: it fires while the overall averages
    still look acceptable.
    """
    if features is None or features.empty:
        return pd.Series(dtype="float64")

    cancel_delta = (
        _col(features, "recent_cancellation_rate") - _col(features, "cancellation_rate")
    ).clip(lower=0.0)
    late_delta = (
        _col(features, "recent_late_delivery_rate") - _col(features, "late_delivery_rate")
    ).clip(lower=0.0)
    delay_delta = (
        _col(features, "recent_shipping_delay") - _col(features, "average_shipping_delay")
    ).clip(lower=0.0)
    activity_drop = (-_col(features, "activity_change")).clip(0.0, 1.0)
    inactivity = _norm(_col(features, "inactive_days"), config.GHOST_INACTIVE_DAYS_CAP)

    score = (
        0.30 * _norm(cancel_delta, 0.15)
        + 0.25 * _norm(late_delta, 0.20)
        + 0.15 * _norm(delay_delta, 3.0)
        + 0.15 * activity_drop
        + 0.15 * inactivity
    )
    return (score * 100.0).clip(0.0, 100.0)


def risk_level(score):
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "LOW"
    for low, high, label in config.RISK_BANDS:
        if low <= value <= high:
            return label
    return config.RISK_BANDS[-1][2]


def compute_risk(features, weights=None):
    """
    Add every score column to a copy of the feature table.

    Expects `anomaly_score` and `ghost_listing_score` to already exist; if they
    do not, they are treated as 0 so the function still returns something
    usable instead of raising.
    """
    out = features.copy()
    if out.empty:
        for col in (
            "behavioral_score", "deterioration_score", "anomaly_score",
            "ghost_listing_score", "risk_score", "risk_level",
        ):
            out[col] = []
        return out

    w = dict(config.RISK_WEIGHTS)
    if weights:
        w.update(weights)

    out["behavioral_score"] = compute_behavioral_score(out).round(2)
    out["deterioration_score"] = compute_deterioration_score(out).round(2)

    anomaly = _col(out, "anomaly_score")
    ghost = _col(out, "ghost_listing_score")

    total_weight = sum(w.values())
    if total_weight <= 0:
        total_weight = 1.0

    risk = (
        w.get("behavioral", 0.0) * out["behavioral_score"]
        + w.get("anomaly", 0.0) * anomaly
        + w.get("ghost", 0.0) * ghost
        + w.get("deterioration", 0.0) * out["deterioration_score"]
    ) / total_weight

    out["risk_score"] = risk.clip(0.0, 100.0).round(2)
    out["risk_level"] = out["risk_score"].map(risk_level)
    return out
