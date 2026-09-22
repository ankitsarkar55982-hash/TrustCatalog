"""
Evaluation of the early-warning system.

Labels come from the OUTCOME WINDOW only; scores come from the FEATURE WINDOW
only. Precision@K answers the question a marketplace operations team actually
asks: "if I only have time to audit 10 sellers today, how many of them really
go on to fail?"
"""

import numpy as np
import pandas as pd

import config


def _prepare(scored, labels, id_column="seller_id"):
    """Join scores with outcome labels; keep only sellers present in both."""
    if scored is None or scored.empty or labels is None or labels.empty:
        return pd.DataFrame()

    keep = [c for c in (id_column, "risk_score", "risk_level") if c in scored.columns]
    merged = scored[keep].merge(labels, on=id_column, how="inner")
    merged = merged.dropna(subset=["risk_score", "label"])
    return merged.sort_values("risk_score", ascending=False).reset_index(drop=True)


def precision_at_k(merged, k):
    """Share of the top-k highest-risk entities that really failed later."""
    if merged.empty:
        return float("nan"), 0
    k = int(min(k, len(merged)))
    if k == 0:
        return float("nan"), 0
    top = merged.head(k)
    return float(top["label"].mean()), k


def evaluate(scored, labels, id_column="seller_id", threshold=None):
    """
    Returns a dict of metrics. Every value is a plain Python float/int so it can
    go straight into SQLite and Streamlit.
    """
    threshold = threshold if threshold is not None else config.HIGH_RISK_THRESHOLD
    merged = _prepare(scored, labels, id_column)

    metrics = {
        "n_evaluated": int(len(merged)),
        "n_positives": 0,
        "base_rate": float("nan"),
        "threshold": float(threshold),
        "precision_at_5": float("nan"),
        "precision_at_10": float("nan"),
        "precision_at_20": float("nan"),
        "precision": float("nan"),
        "recall": float("nan"),
        "f1": float("nan"),
        "lift_at_10": float("nan"),
        "median_lead_time_days": float("nan"),
    }

    if merged.empty:
        metrics["note"] = (
            "No sellers appear in both the feature window and the outcome window. "
            "Widen the dataset or lower MIN_OUTCOME_ORDERS."
        )
        return metrics, merged

    positives = int(merged["label"].sum())
    base_rate = float(merged["label"].mean())
    metrics["n_positives"] = positives
    metrics["base_rate"] = base_rate

    for k in (5, 10, 20):
        value, used_k = precision_at_k(merged, k)
        metrics[f"precision_at_{k}"] = value
        metrics[f"k_used_{k}"] = used_k

    if not np.isnan(metrics["precision_at_10"]) and base_rate > 0:
        metrics["lift_at_10"] = metrics["precision_at_10"] / base_rate

    # Threshold-based classification metrics.
    predicted = (merged["risk_score"] >= threshold).astype(int)
    actual = merged["label"].astype(int)

    tp = int(((predicted == 1) & (actual == 1)).sum())
    fp = int(((predicted == 1) & (actual == 0)).sum())
    fn = int(((predicted == 0) & (actual == 1)).sum())
    tn = int(((predicted == 0) & (actual == 0)).sum())

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    if precision and recall and not (np.isnan(precision) or np.isnan(recall)) and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = float("nan")

    metrics.update(
        {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "true_negatives": tn,
            "n_flagged": int(predicted.sum()),
        }
    )
    return metrics, merged


def metrics_to_frame(metrics):
    """Flatten the metrics dict into a display-friendly two-column frame."""
    pretty = {
        "n_evaluated": "Sellers evaluated",
        "n_positives": "Sellers that actually failed later",
        "base_rate": "Base failure rate",
        "precision_at_5": "Precision@5",
        "precision_at_10": "Precision@10",
        "precision_at_20": "Precision@20",
        "precision": "Precision (score >= threshold)",
        "recall": "Recall",
        "f1": "F1-score",
        "lift_at_10": "Lift @10 vs random",
    }
    rows = []
    for key, label in pretty.items():
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, float) and np.isnan(value):
            shown = "n/a"
        elif key in ("base_rate", "precision_at_5", "precision_at_10",
                     "precision_at_20", "precision", "recall", "f1"):
            shown = f"{value:.3f}"
        elif key == "lift_at_10":
            shown = f"{value:.2f}x"
        else:
            shown = f"{value:,}"
        rows.append({"Metric": label, "Value": shown})
    return pd.DataFrame(rows)
