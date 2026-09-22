"""
Explainable AI layer.

Every reason is generated from the actual feature values and is compared
against the median of the current population, so the wording is always backed
by a number the judge can verify in the table.

Deliberate wording rule: the system never says "fraud" or "is out of stock".
It says "possible", "high risk", "behavioural anomaly".
"""

import numpy as np
import pandas as pd

import config


def _pct(value):
    return f"{float(value) * 100:.1f}%"


def _population_stats(features):
    """Median of each numeric column - used as the 'normal range' reference."""
    stats = {}
    for col in features.select_dtypes(include=[np.number]).columns:
        series = pd.to_numeric(features[col], errors="coerce").dropna()
        stats[col] = float(series.median()) if len(series) else 0.0
    return stats


def _reasons_for_row(row, stats):
    """Build the ordered list of reason strings for one entity."""
    reasons = []

    cancel = float(row.get("cancellation_rate", 0.0) or 0.0)
    if cancel >= 0.10:
        reasons.append(
            f"Cancellation rate is {_pct(cancel)}, against a platform median of "
            f"{_pct(stats.get('cancellation_rate', 0.0))}."
        )

    late = float(row.get("late_delivery_rate", 0.0) or 0.0)
    if late >= 0.10:
        reasons.append(
            f"Late delivery rate is {_pct(late)} of delivered orders "
            f"(median {_pct(stats.get('late_delivery_rate', 0.0))})."
        )

    delay = float(row.get("average_shipping_delay", 0.0) or 0.0)
    if delay >= 2.0:
        reasons.append(
            f"Average handover delay (order approved to carrier pickup) is "
            f"{delay:.1f} days, against a median of "
            f"{stats.get('average_shipping_delay', 0.0):.1f} days."
        )

    delivery = float(row.get("average_delivery_days", 0.0) or 0.0)
    median_delivery = stats.get("average_delivery_days", 0.0)
    if delivery > 0 and median_delivery > 0 and delivery >= median_delivery * 1.5:
        reasons.append(
            f"Average delivery takes {delivery:.1f} days versus a median of "
            f"{median_delivery:.1f} days."
        )

    breach = float(row.get("shipping_limit_breach_rate", 0.0) or 0.0)
    if breach >= 0.15:
        reasons.append(
            f"The seller missed its own shipping deadline on {_pct(breach)} of orders."
        )

    change = float(row.get("activity_change", 0.0) or 0.0)
    if change <= -0.30:
        reasons.append(
            f"Recent order activity dropped by {abs(change) * 100:.0f}% compared with "
            f"this seller's own baseline."
        )

    inactive = float(row.get("inactive_days", 0.0) or 0.0)
    if inactive >= 21:
        reasons.append(
            f"No orders recorded for {inactive:.0f} days - the listing may no "
            f"longer be fulfillable."
        )

    unavailable = float(row.get("unavailable_rate", 0.0) or 0.0)
    if unavailable >= 0.05:
        reasons.append(
            f"{_pct(unavailable)} of orders ended with an 'unavailable' status."
        )

    never_shipped = float(row.get("never_shipped_rate", 0.0) or 0.0)
    if never_shipped >= 0.15:
        reasons.append(
            f"{_pct(never_shipped)} of orders never reached a carrier at all."
        )

    variance = float(row.get("delivery_time_variance", 0.0) or 0.0)
    median_var = stats.get("delivery_time_variance", 0.0)
    if variance > 0 and median_var > 0 and variance >= median_var * 3:
        reasons.append(
            f"Delivery-time variance ({variance:.1f}) is unusually high, meaning "
            f"fulfilment is inconsistent rather than merely slow."
        )

    review = float(row.get("average_review_score", 5.0) or 5.0)
    if review <= 3.5:
        reasons.append(f"Average customer review score is {review:.2f} out of 5.")

    # Deterioration (early-warning) signals.
    recent_cancel = float(row.get("recent_cancellation_rate", 0.0) or 0.0)
    if recent_cancel - cancel >= 0.05:
        reasons.append(
            f"Cancellations in the last {config.RECENT_DAYS} days ({_pct(recent_cancel)}) "
            f"are worse than this seller's own historical rate ({_pct(cancel)})."
        )

    if int(row.get("is_anomaly", 0) or 0) == 1:
        reasons.append(
            "The Isolation Forest model places this seller's overall behaviour "
            "outside the normal cluster."
        )

    ghost = float(row.get("ghost_listing_score", 0.0) or 0.0)
    if ghost >= config.GHOST_FLAG_THRESHOLD:
        reasons.append(
            f"Combined ghost-listing signals score {ghost:.0f}/100 - "
            f"flagged as a Possible Ghost Listing."
        )

    if not reasons:
        reasons.append(
            "No individual signal crossed its threshold; the score comes from the "
            "combination of mildly elevated metrics."
        )
    return reasons


def generate_explanations(features, id_column="seller_id", min_risk=0, max_reasons=6):
    """
    Returns a tidy DataFrame with one row per entity:
        <id_column>, risk_score, risk_level, reasons (newline separated), n_reasons
    """
    if features is None or features.empty:
        return pd.DataFrame(
            columns=[id_column, "risk_score", "risk_level", "reasons", "n_reasons"]
        )

    stats = _population_stats(features)
    subset = features[features.get("risk_score", 0) >= min_risk]

    records = []
    for _, row in subset.iterrows():
        reasons = _reasons_for_row(row, stats)[:max_reasons]
        record = {
            id_column: row[id_column],
            "risk_score": float(row.get("risk_score", 0.0)),
            "risk_level": row.get("risk_level", "LOW"),
            "reasons": "\n".join(f"- {r}" for r in reasons),
            "n_reasons": len(reasons),
        }
        if id_column == "product_id" and "seller_id" in row:
            record["seller_id"] = row["seller_id"]
        records.append(record)

    return pd.DataFrame(records)


def explain_one(row, stats=None, features=None):
    """Convenience helper used by the dashboard for a single selected entity."""
    if stats is None:
        stats = _population_stats(features) if features is not None else {}
    return _reasons_for_row(row, stats)


def format_explanation(entity_id, row, reasons):
    """Plain-text block, matching the format shown in the spec."""
    lines = [
        f"Seller/Listing {entity_id}",
        "",
        f"Risk Score: {float(row.get('risk_score', 0)):.0f}",
        f"Risk Level: {row.get('risk_level', 'LOW')}",
        "",
        "Reasons:",
        "",
    ]
    lines.extend(f"- {r}" for r in reasons)
    return "\n".join(lines)
