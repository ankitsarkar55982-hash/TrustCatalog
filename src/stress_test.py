"""
Judge stress test (spec section 13).

Takes the real seller feature table, samples a set of normal sellers, and
artificially degrades a subset of them IN MEMORY ONLY. Nothing on disk is
modified, so the test can be run repeatedly during a demo.

The degraded rows are then pushed through exactly the same scoring path as
production: anomaly model -> ghost score -> risk score. If the pipeline were
hard-coded, the degraded sellers would not move - which is the point.
"""

import numpy as np
import pandas as pd

import config
from src import anomaly_detection, ghost_listing, risk_scoring


def _apply_profile(df, profile, rng, jitter=0.15):
    """Overwrite the driving features with a profile, plus a little noise."""
    n = len(df)
    for column, target in profile.items():
        noise = 1.0 + rng.normal(0.0, jitter, size=n)
        values = np.clip(np.asarray(target, dtype="float64") * noise, 0.0, None)
        if column.endswith("_rate"):
            values = np.clip(values, 0.0, 1.0)
        df[column] = values
    return df


def _derive_dependent_features(df, degraded, rng):
    """
    Keep the row internally consistent. A seller with a 25% cancellation rate
    should also show the secondary symptoms, otherwise the injected row is not
    a realistic seller and the test proves nothing.
    """
    n = len(df)
    if degraded:
        df["average_shipping_delay"] = np.clip(
            df["average_delivery_days"] * 0.45 + rng.normal(0, 0.4, n), 0, None
        )
        df["recent_cancellation_rate"] = np.clip(
            df["cancellation_rate"] * rng.uniform(1.1, 1.6, n), 0, 1
        )
        df["recent_late_delivery_rate"] = np.clip(
            df["late_delivery_rate"] * rng.uniform(1.1, 1.5, n), 0, 1
        )
        df["recent_shipping_delay"] = df["average_shipping_delay"] * rng.uniform(1.1, 1.6, n)
        df["average_review_score"] = np.clip(rng.normal(2.3, 0.5, n), 1.0, 5.0)
        df["activity_change"] = np.clip(rng.normal(-0.55, 0.2, n), -1.0, 3.0)
        df["inactive_days"] = np.clip(rng.normal(18, 8, n), 0, None)
        df["unavailable_rate"] = np.clip(df["cancellation_rate"] * 0.6, 0, 1)
        df["never_shipped_rate"] = np.clip(df["cancellation_rate"] * 0.8, 0, 1)
        df["shipping_limit_breach_rate"] = np.clip(
            df["late_delivery_rate"] * rng.uniform(0.8, 1.2, n), 0, 1
        )
        df["delivery_time_variance"] = np.clip(rng.normal(60, 20, n), 1, None)
        df["shipping_delay_variance"] = np.clip(rng.normal(12, 4, n), 0.5, None)
    else:
        df["average_shipping_delay"] = np.clip(
            df["average_delivery_days"] * 0.20 + rng.normal(0, 0.2, n), 0, None
        )
        df["recent_cancellation_rate"] = np.clip(
            df["cancellation_rate"] * rng.uniform(0.8, 1.2, n), 0, 1
        )
        df["recent_late_delivery_rate"] = np.clip(
            df["late_delivery_rate"] * rng.uniform(0.8, 1.2, n), 0, 1
        )
        df["recent_shipping_delay"] = df["average_shipping_delay"] * rng.uniform(0.8, 1.2, n)
        df["average_review_score"] = np.clip(rng.normal(4.4, 0.3, n), 1.0, 5.0)
        df["activity_change"] = np.clip(rng.normal(0.02, 0.15, n), -1.0, 3.0)
        df["inactive_days"] = np.clip(rng.normal(3, 2, n), 0, None)
        df["unavailable_rate"] = np.clip(df["cancellation_rate"] * 0.3, 0, 1)
        df["never_shipped_rate"] = np.clip(df["cancellation_rate"] * 0.4, 0, 1)
        df["shipping_limit_breach_rate"] = np.clip(
            df["late_delivery_rate"] * rng.uniform(0.6, 1.0, n), 0, 1
        )
        df["delivery_time_variance"] = np.clip(rng.normal(6, 2, n), 0.5, None)
        df["shipping_delay_variance"] = np.clip(rng.normal(1.5, 0.6, n), 0.1, None)
    return df


def build_stress_population(seller_features, settings=None, rng=None):
    """Create the mixed population of normal + artificially degraded sellers."""
    settings = {**config.STRESS_TEST_CONFIG, **(settings or {})}
    if rng is None:
        rng = np.random.default_rng(settings["random_seed"])

    n_normal = int(settings["n_normal"])
    n_degraded = int(settings["n_degraded"])

    if seller_features is None or seller_features.empty:
        raise ValueError(
            "Run the pipeline first - there are no seller features to build the "
            "stress-test population from."
        )

    template = seller_features.copy()
    needed = n_normal + n_degraded
    replace = len(template) < needed
    sample = template.sample(n=needed, replace=replace, random_state=settings["random_seed"])
    sample = sample.reset_index(drop=True)

    normal = sample.iloc[:n_normal].copy()
    degraded = sample.iloc[n_normal:].copy()

    normal = _apply_profile(normal, settings["normal_profile"], rng)
    normal = _derive_dependent_features(normal, degraded=False, rng=rng)
    normal["seller_id"] = [f"STRESS_NORMAL_{i:03d}" for i in range(len(normal))]
    normal["injected_type"] = "NORMAL"

    degraded = _apply_profile(degraded, settings["degraded_profile"], rng)
    degraded = _derive_dependent_features(degraded, degraded=True, rng=rng)
    degraded["seller_id"] = [f"STRESS_DEGRADED_{i:03d}" for i in range(len(degraded))]
    degraded["injected_type"] = "DEGRADED"

    population = pd.concat([normal, degraded], ignore_index=True)
    population["total_orders"] = population["total_orders"].clip(lower=config.MIN_ORDERS_FOR_SCORING)
    return population


def run_stress_test(seller_features, model_bundle, settings=None):
    """
    Score the injected population and report detection quality.

    Returns (summary_dict, scored_population_dataframe).
    """
    settings = {**config.STRESS_TEST_CONFIG, **(settings or {})}
    threshold = float(settings["detection_threshold"])

    population = build_stress_population(seller_features, settings)

    if model_bundle is None:
        population["anomaly_score"] = 0.0
        population["is_anomaly"] = 0
        model_note = "Anomaly model unavailable - scored on rules only."
    else:
        population = anomaly_detection.score(population, model_bundle)
        model_note = ""

    population = ghost_listing.add_ghost_scores(population)
    population = risk_scoring.compute_risk(population)

    population["detected"] = (population["risk_score"] >= threshold).astype(int)
    population["is_degraded"] = (population["injected_type"] == "DEGRADED").astype(int)

    degraded = population[population["is_degraded"] == 1]
    normal = population[population["is_degraded"] == 0]

    tp = int(degraded["detected"].sum())
    fn = int(len(degraded) - tp)
    fp = int(normal["detected"].sum())
    tn = int(len(normal) - fp)

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    detection_rate = recall

    summary = {
        "n_normal": int(len(normal)),
        "n_degraded": int(len(degraded)),
        "n_detected": tp,
        "n_missed": fn,
        "false_alarms": fp,
        "true_negatives": tn,
        "detection_rate": float(detection_rate) if detection_rate == detection_rate else float("nan"),
        "precision": float(precision) if precision == precision else float("nan"),
        "recall": float(recall) if recall == recall else float("nan"),
        "threshold": threshold,
        "avg_risk_degraded": float(degraded["risk_score"].mean()) if len(degraded) else float("nan"),
        "avg_risk_normal": float(normal["risk_score"].mean()) if len(normal) else float("nan"),
        "note": model_note,
    }
    if not np.isnan(summary["precision"]) and not np.isnan(summary["recall"]) \
            and (summary["precision"] + summary["recall"]) > 0:
        summary["f1"] = 2 * summary["precision"] * summary["recall"] / (
            summary["precision"] + summary["recall"]
        )
    else:
        summary["f1"] = float("nan")

    columns = [
        "seller_id", "injected_type", "cancellation_rate", "late_delivery_rate",
        "average_delivery_days", "average_shipping_delay", "anomaly_score",
        "ghost_listing_score", "risk_score", "risk_level", "detected",
    ]
    columns = [c for c in columns if c in population.columns]
    return summary, population[columns].sort_values("risk_score", ascending=False)
