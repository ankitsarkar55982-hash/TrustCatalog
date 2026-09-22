"""
TrustCatalog risk-intelligence admin module.

This is the ORIGINAL risk-pipeline dashboard (Overview / Seller Risk /
Product Risk / Ghost Listings / Seller Details / Analytics / Evaluation /
Judge Stress Test), preserved and reused as-is, unchanged in behaviour.

It is no longer launched directly - dashboard/pages/5_Admin_Center.py
calls render_risk_admin() after doing its own login + role check, so an
ADMIN sees exactly these pages as one tab of the Admin Center. Every
number on screen still comes straight from the SQLite database written
by scripts/run_pipeline.py; nothing here is hard-coded and no model is
retrained (except inside the stress test, which only *scores*).
"""

import subprocess
import sys
from pathlib import Path

# Make `config` and `src` importable when Streamlit runs this file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import config
from src import anomaly_detection, database, evaluation, explanations as explain_mod
from src import stress_test as stress_mod

RISK_COLORS = {
    "LOW": "#22c55e",
    "MEDIUM": "#eab308",
    "HIGH": "#f97316",
    "CRITICAL": "#ef4444",
}
PLOT_TEMPLATE = "plotly_dark"

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; }
      .tc-title { font-size: 2.3rem; font-weight: 800; letter-spacing: .12em;
                  color: #e2e8f0; margin-bottom: 0; }
      .tc-sub   { color: #94a3b8; font-size: 1rem; margin-top: .2rem; }
      .tc-note  { color: #64748b; font-size: .85rem; }
      div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# DATA ACCESS
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading results from database...")
def load_data(db_mtime):
    """db_mtime is only there to invalidate the cache when the DB changes."""
    return database.load_all()


def db_signature():
    return config.DB_PATH.stat().st_mtime if config.DB_PATH.exists() else 0.0


def fmt_pct(value, digits=1):
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def fmt_num(value, digits=2):
    try:
        v = float(value)
        return "n/a" if np.isnan(v) else f"{v:.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def run_script(relative_path, extra_args=None):
    """Run a project script and stream the result into the UI."""
    cmd = [sys.executable, str(ROOT / relative_path)] + (extra_args or [])
    with st.spinner(f"Running {relative_path} ..."):
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if proc.returncode == 0:
        st.success(f"{relative_path} finished.")
    else:
        st.error(f"{relative_path} failed (exit code {proc.returncode}).")
    with st.expander("Console output"):
        st.code((proc.stdout or "") + "\n" + (proc.stderr or ""), language="text")
    return proc.returncode == 0


def empty_state():
    """Shown when the database has not been built yet."""
    st.warning("No pipeline results found yet.")
    st.markdown(
        """
**TrustCatalog has not been run on this machine yet.** Two ways forward:

**Option A - use the buttons below** (easiest).

**Option B - run these in the VS Code terminal:**

```
python scripts/generate_demo_data.py
python scripts/run_pipeline.py
```
        """
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("1. Generate DEMO data", use_container_width=True):
            if run_script("scripts/generate_demo_data.py"):
                st.cache_data.clear()
    with col2:
        if st.button("2. Run the pipeline", type="primary", use_container_width=True):
            if run_script("scripts/run_pipeline.py"):
                st.cache_data.clear()
                st.rerun()


def risk_badge(level):
    color = RISK_COLORS.get(level, "#94a3b8")
    return (
        f"<span style='background:{color};color:#0f172a;padding:3px 12px;"
        f"border-radius:999px;font-weight:700;font-size:.85rem'>{level}</span>"
    )


def style_risk_table(df, score_col="risk_score"):
    """Colour the risk column. Falls back silently if styling is unavailable."""
    if df.empty or score_col not in df.columns:
        return df
    try:
        return df.style.background_gradient(
            subset=[score_col], cmap="RdYlGn_r", vmin=0, vmax=100
        ).format(precision=2)
    except Exception:                               # noqa: BLE001
        return df


# ---------------------------------------------------------------------------
# PAGES
# ---------------------------------------------------------------------------
def page_overview(data, meta):
    sellers = data["seller_features"]
    products = data["product_features"]
    trends = data.get("daily_trends", pd.DataFrame())

    st.subheader("Overview")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Sellers", f"{meta.get('n_sellers', len(sellers)):,}")
    c2.metric("Total Products", f"{meta.get('n_products', len(products)):,}")
    c3.metric("Total Orders", f"{meta.get('n_orders', 0):,}")
    c4.metric("Average Risk Score", fmt_num(sellers["risk_score"].mean()))

    high = int((sellers["risk_level"] == "HIGH").sum())
    critical = int((sellers["risk_level"] == "CRITICAL").sum())
    ghosts = int(products["possible_ghost_listing"].sum()) if not products.empty else 0

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("High Risk Sellers", f"{high:,}")
    c6.metric("Critical Risk Sellers", f"{critical:,}")
    c7.metric("Possible Ghost Listings", f"{ghosts:,}")
    c8.metric("Scored Listings", f"{len(products):,}")

    st.divider()

    left, right = st.columns(2)
    with left:
        st.markdown("**Risk distribution**")
        counts = (
            sellers["risk_level"].value_counts()
            .reindex(["LOW", "MEDIUM", "HIGH", "CRITICAL"])
            .fillna(0)
            .reset_index()
        )
        counts.columns = ["risk_level", "sellers"]
        fig = px.bar(
            counts, x="risk_level", y="sellers", color="risk_level",
            color_discrete_map=RISK_COLORS, template=PLOT_TEMPLATE,
        )
        fig.update_layout(showlegend=False, height=340,
                          xaxis_title="", yaxis_title="Sellers")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Risk score histogram**")
        fig = px.histogram(
            sellers, x="risk_score", nbins=25, template=PLOT_TEMPLATE,
            color_discrete_sequence=["#38bdf8"],
        )
        fig.add_vline(x=config.HIGH_RISK_THRESHOLD, line_dash="dash",
                      line_color="#f97316", annotation_text="HIGH")
        fig.update_layout(height=340, xaxis_title="Risk score", yaxis_title="Sellers")
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.markdown("**Top 10 risky sellers**")
        top = sellers.nlargest(10, "risk_score")[["seller_id", "risk_score", "risk_level"]]
        fig = px.bar(
            top.sort_values("risk_score"), x="risk_score", y="seller_id",
            orientation="h", color="risk_level", color_discrete_map=RISK_COLORS,
            template=PLOT_TEMPLATE,
        )
        fig.update_layout(height=380, yaxis_title="", xaxis_title="Risk score")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Top 10 risky listings**")
        if products.empty:
            st.info("No listing-level results available.")
        else:
            top = products.nlargest(10, "risk_score")[
                ["product_id", "risk_score", "risk_level"]
            ]
            fig = px.bar(
                top.sort_values("risk_score"), x="risk_score", y="product_id",
                orientation="h", color="risk_level", color_discrete_map=RISK_COLORS,
                template=PLOT_TEMPLATE,
            )
            fig.update_layout(height=380, yaxis_title="", xaxis_title="Risk score")
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown("**Platform trends**")
    if trends.empty:
        st.info("Trend data is not available for this run.")
        return

    trends = trends.copy()
    trends["date"] = pd.to_datetime(trends["date"], errors="coerce")
    trends = trends.dropna(subset=["date"]).sort_values("date")
    trends["cancellation_rate_pct"] = trends["cancellation_rate"] * 100
    trends["late_delivery_rate_pct"] = trends["late_delivery_rate"] * 100

    t1, t2, t3 = st.tabs(["Cancellations", "Delivery delay", "Seller activity"])
    with t1:
        fig = px.line(trends, x="date", y="cancellation_rate_pct",
                      template=PLOT_TEMPLATE, color_discrete_sequence=["#ef4444"])
        fig.update_layout(height=320, yaxis_title="Cancellation rate (%)", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)
    with t2:
        fig = px.line(trends, x="date", y=["avg_delivery_days", "avg_shipping_delay"],
                      template=PLOT_TEMPLATE)
        fig.update_layout(height=320, yaxis_title="Days", xaxis_title="",
                          legend_title="")
        st.plotly_chart(fig, use_container_width=True)
    with t3:
        fig = px.area(trends, x="date", y="orders", template=PLOT_TEMPLATE,
                      color_discrete_sequence=["#38bdf8"])
        fig.update_layout(height=320, yaxis_title="Orders per day", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)


def _risk_filters(df, key_prefix):
    """Shared filter widgets. Returns the filtered frame."""
    c1, c2, c3 = st.columns([1.2, 1, 1.4])
    with c1:
        levels = st.multiselect(
            "Risk level",
            ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
            default=["HIGH", "CRITICAL"],
            key=f"{key_prefix}_levels",
        )
    with c2:
        min_score = st.slider(
            "Minimum risk score", 0, 100, 0, step=1, key=f"{key_prefix}_min"
        )
    with c3:
        search = st.text_input(
            "Search ID (partial match)", "", key=f"{key_prefix}_search"
        )

    out = df.copy()
    if levels:
        out = out[out["risk_level"].isin(levels)]
    out = out[out["risk_score"] >= min_score]

    if search.strip():
        term = search.strip().lower()
        id_cols = [c for c in ("seller_id", "product_id") if c in out.columns]
        if id_cols:
            mask = pd.Series(False, index=out.index)
            for col in id_cols:
                mask = mask | out[col].astype(str).str.lower().str.contains(term, na=False)
            out = out[mask]
    return out.sort_values("risk_score", ascending=False)


def page_seller_risk(data):
    sellers = data["seller_features"]
    st.subheader("Seller Risk")
    st.caption(
        "Sellers ranked by combined risk. Nothing here asserts fraud - these are "
        "behavioural risk signals for a human auditor to review."
    )

    filtered = _risk_filters(sellers, "seller")

    columns = [
        "seller_id", "total_orders", "cancellation_rate", "late_delivery_rate",
        "average_delivery_days", "average_review_score", "anomaly_score",
        "ghost_listing_score", "risk_score", "risk_level",
    ]
    columns = [c for c in columns if c in filtered.columns]

    st.markdown(f"**{len(filtered):,} sellers match the filters**")
    if filtered.empty:
        st.info("No sellers match. Try lowering the minimum score or adding risk levels.")
        return

    view = filtered[columns].copy()
    for col in ("cancellation_rate", "late_delivery_rate"):
        if col in view.columns:
            view[col] = (view[col] * 100).round(1)
    view = view.rename(
        columns={
            "seller_id": "Seller ID",
            "total_orders": "Orders",
            "cancellation_rate": "Cancel %",
            "late_delivery_rate": "Late %",
            "average_delivery_days": "Avg delivery (d)",
            "average_review_score": "Avg review",
            "anomaly_score": "Anomaly",
            "ghost_listing_score": "Ghost",
            "risk_score": "Risk",
            "risk_level": "Level",
        }
    )
    st.dataframe(style_risk_table(view, "Risk"), use_container_width=True,
                 hide_index=True, height=520)

    st.download_button(
        "Download this view as CSV",
        data=filtered[columns].to_csv(index=False).encode("utf-8"),
        file_name="trustcatalog_seller_risk.csv",
        mime="text/csv",
    )


def page_seller_details(data):
    sellers = data["seller_features"]
    explanations = data["risk_explanations"]
    products = data["product_features"]

    st.subheader("Seller Details")
    if sellers.empty:
        st.info("No sellers available.")
        return

    ordered = sellers.sort_values("risk_score", ascending=False)
    options = ordered["seller_id"].tolist()
    labels = {
        sid: f"{sid}  -  risk {score:.0f} ({level})"
        for sid, score, level in zip(
            ordered["seller_id"], ordered["risk_score"], ordered["risk_level"]
        )
    }
    seller_id = st.selectbox(
        "Select a seller (sorted by risk)", options, format_func=lambda s: labels.get(s, s)
    )

    row = sellers[sellers["seller_id"] == seller_id]
    if row.empty:
        st.warning("Seller not found.")
        return
    row = row.iloc[0]

    st.markdown(
        f"### {seller_id} &nbsp; {risk_badge(row['risk_level'])}",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Risk Score", f"{row['risk_score']:.1f}")
    c2.metric("Total Orders", f"{int(row['total_orders']):,}")
    c3.metric("Cancellation Rate", fmt_pct(row["cancellation_rate"]))
    c4.metric("Late Delivery Rate", fmt_pct(row["late_delivery_rate"]))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Avg Delivery Time", f"{row['average_delivery_days']:.1f} d")
    c6.metric("Avg Handover Delay", f"{row['average_shipping_delay']:.1f} d")
    c7.metric("Avg Review Score", f"{row['average_review_score']:.2f}")
    c8.metric("Days Since Last Order", f"{int(row['inactive_days'])}")

    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Anomaly Score", f"{row['anomaly_score']:.1f}")
    c10.metric("Ghost Score", f"{row['ghost_listing_score']:.1f}")
    c11.metric("Behavioural Score", f"{row['behavioral_score']:.1f}")
    c12.metric("Deterioration", f"{row['deterioration_score']:.1f}")

    st.divider()
    left, right = st.columns([1.1, 1])

    with left:
        st.markdown("#### Why this seller was flagged")
        reasons = None
        if not explanations.empty and "seller_id" in explanations.columns:
            match = explanations[explanations["seller_id"] == seller_id]
            if not match.empty:
                reasons = match.iloc[0]["reasons"]
        if reasons:
            st.markdown(reasons)
        else:
            for reason in explain_mod.explain_one(row, features=sellers):
                st.markdown(f"- {reason}")

    with right:
        st.markdown("#### Risk composition")
        parts = pd.DataFrame(
            {
                "component": ["Behavioural", "ML anomaly", "Ghost", "Deterioration"],
                "score": [
                    float(row["behavioral_score"]),
                    float(row["anomaly_score"]),
                    float(row["ghost_listing_score"]),
                    float(row["deterioration_score"]),
                ],
                "weight": [
                    config.RISK_WEIGHTS["behavioral"],
                    config.RISK_WEIGHTS["anomaly"],
                    config.RISK_WEIGHTS["ghost"],
                    config.RISK_WEIGHTS["deterioration"],
                ],
            }
        )
        parts["contribution"] = (parts["score"] * parts["weight"]).round(2)
        fig = px.bar(
            parts, x="contribution", y="component", orientation="h",
            template=PLOT_TEMPLATE, color_discrete_sequence=["#818cf8"],
            hover_data=["score", "weight"],
        )
        fig.update_layout(height=280, yaxis_title="", xaxis_title="Points contributed")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### This seller vs the platform median")
    compare_cols = [
        "cancellation_rate", "late_delivery_rate", "average_shipping_delay",
        "average_delivery_days",
    ]
    compare_cols = [c for c in compare_cols if c in sellers.columns]
    comparison = pd.DataFrame(
        {
            "metric": compare_cols,
            "this_seller": [float(row[c]) for c in compare_cols],
            "platform_median": [float(sellers[c].median()) for c in compare_cols],
        }
    )
    melted = comparison.melt(id_vars="metric", var_name="series", value_name="value")
    fig = px.bar(melted, x="metric", y="value", color="series", barmode="group",
                 template=PLOT_TEMPLATE)
    fig.update_layout(height=330, xaxis_title="", yaxis_title="", legend_title="")
    st.plotly_chart(fig, use_container_width=True)

    if not products.empty:
        own = products[products["seller_id"] == seller_id]
        if not own.empty:
            st.markdown("#### Listings from this seller")
            cols = [c for c in ("product_id", "total_orders", "cancellation_rate",
                                "late_delivery_rate", "ghost_listing_score",
                                "risk_score", "risk_level") if c in own.columns]
            st.dataframe(
                own[cols].sort_values("risk_score", ascending=False),
                use_container_width=True, hide_index=True, height=300,
            )


def page_product_risk(data):
    products = data["product_features"]
    st.subheader("Product / Listing Risk")
    if products.empty:
        st.info("No listing-level results available for this run.")
        return

    filtered = _risk_filters(products, "product")
    st.markdown(f"**{len(filtered):,} listings match the filters**")
    if filtered.empty:
        st.info("No listings match the current filters.")
        return

    cols = [c for c in (
        "product_id", "seller_id", "category", "total_orders", "cancellation_rate",
        "late_delivery_rate", "average_delivery_days", "ghost_listing_score",
        "risk_score", "risk_level", "possible_ghost_listing",
    ) if c in filtered.columns]

    st.dataframe(style_risk_table(filtered[cols], "risk_score"),
                 use_container_width=True, hide_index=True, height=520)
    st.download_button(
        "Download this view as CSV",
        data=filtered[cols].to_csv(index=False).encode("utf-8"),
        file_name="trustcatalog_product_risk.csv",
        mime="text/csv",
    )


def page_ghost_listings(data):
    products = data["product_features"]
    explanations = data["product_explanations"]

    st.subheader("Possible Ghost Listings")
    st.caption(
        f"Listings whose combined behavioural signals score at or above "
        f"{config.GHOST_FLAG_THRESHOLD}/100. This is a *concern level*, not an "
        f"accusation - the seller may simply have stopped restocking."
    )

    if products.empty:
        st.info("No listing-level results available.")
        return

    c1, c2 = st.columns([1, 2])
    with c1:
        min_ghost = st.slider(
            "Minimum ghost score", 0, 100, int(config.GHOST_FLAG_THRESHOLD), step=1
        )
    with c2:
        categories = sorted(products["category"].dropna().astype(str).unique().tolist()) \
            if "category" in products.columns else []
        chosen = st.multiselect("Category", categories, default=[])

    flagged = products[products["ghost_listing_score"] >= min_ghost].copy()
    if chosen and "category" in flagged.columns:
        flagged = flagged[flagged["category"].astype(str).isin(chosen)]
    flagged = flagged.sort_values("ghost_listing_score", ascending=False)

    st.markdown(f"**{len(flagged):,} listings flagged**")
    if flagged.empty:
        st.success("Nothing crosses the threshold at this setting.")
        return

    if not explanations.empty and "product_id" in explanations.columns:
        flagged = flagged.merge(
            explanations[["product_id", "reasons"]], on="product_id", how="left"
        )

    cols = [c for c in (
        "product_id", "seller_id", "ghost_listing_score", "ghost_level", "risk_score",
        "cancellation_rate", "average_shipping_delay", "activity_change",
        "inactive_days",
    ) if c in flagged.columns]

    st.dataframe(style_risk_table(flagged[cols], "ghost_listing_score"),
                 use_container_width=True, hide_index=True, height=420)

    st.markdown("#### Inspect one flagged listing")
    pick = st.selectbox("Listing", flagged["product_id"].tolist())
    row = flagged[flagged["product_id"] == pick].iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Ghost score", f"{row['ghost_listing_score']:.1f}")
    c2.metric("Risk score", f"{row['risk_score']:.1f}")
    c3.metric("Days inactive", f"{int(row.get('inactive_days', 0))}")

    if "reasons" in row and isinstance(row["reasons"], str) and row["reasons"].strip():
        st.markdown(row["reasons"])
    else:
        for reason in explain_mod.explain_one(row, features=products):
            st.markdown(f"- {reason}")


def page_analytics(data):
    sellers = data["seller_features"]
    products = data["product_features"]
    st.subheader("Analytics")

    if sellers.empty:
        st.info("No data to analyse.")
        return

    c1, c2 = st.columns(2)
    with c1:
        fig = px.histogram(sellers, x="cancellation_rate", nbins=30,
                           template=PLOT_TEMPLATE,
                           color_discrete_sequence=["#f87171"])
        fig.update_layout(title="Cancellation rate distribution", height=320,
                          xaxis_title="Cancellation rate", yaxis_title="Sellers")
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.histogram(sellers, x="average_delivery_days", nbins=30,
                           template=PLOT_TEMPLATE,
                           color_discrete_sequence=["#60a5fa"])
        fig.update_layout(title="Average delivery days distribution", height=320,
                          xaxis_title="Days", yaxis_title="Sellers")
        st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        fig = px.histogram(sellers, x="average_review_score", nbins=20,
                           template=PLOT_TEMPLATE,
                           color_discrete_sequence=["#a78bfa"])
        fig.update_layout(title="Review score distribution", height=320,
                          xaxis_title="Average review", yaxis_title="Sellers")
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.histogram(sellers, x="anomaly_score", nbins=25,
                           template=PLOT_TEMPLATE,
                           color_discrete_sequence=["#34d399"])
        fig.update_layout(title="Anomaly score distribution", height=320,
                          xaxis_title="Anomaly score", yaxis_title="Sellers")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Does the score track real behaviour?")
    c1, c2 = st.columns(2)
    with c1:
        fig = px.scatter(
            sellers, x="cancellation_rate", y="risk_score", color="risk_level",
            color_discrete_map=RISK_COLORS, template=PLOT_TEMPLATE,
            hover_data=["seller_id", "total_orders"],
        )
        fig.update_layout(title="Risk vs cancellation rate", height=360)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.scatter(
            sellers, x="average_shipping_delay", y="risk_score", color="risk_level",
            color_discrete_map=RISK_COLORS, template=PLOT_TEMPLATE,
            hover_data=["seller_id", "total_orders"],
        )
        fig.update_layout(title="Risk vs handover delay", height=360)
        st.plotly_chart(fig, use_container_width=True)

    if not products.empty and "category" in products.columns:
        st.markdown("#### Riskiest categories")
        by_cat = (
            products.groupby("category", as_index=False)
            .agg(avg_risk=("risk_score", "mean"), listings=("product_id", "count"))
            .sort_values("avg_risk", ascending=False)
            .head(12)
        )
        fig = px.bar(by_cat, x="avg_risk", y="category", orientation="h",
                     template=PLOT_TEMPLATE, color="avg_risk",
                     color_continuous_scale="Reds", hover_data=["listings"])
        fig.update_layout(height=420, yaxis_title="", xaxis_title="Average risk score")
        st.plotly_chart(fig, use_container_width=True)


def page_evaluation(data, meta):
    st.subheader("Early-Warning Evaluation")
    metrics_df = data["evaluation_metrics"]
    detail = data.get("evaluation_detail", pd.DataFrame())

    st.markdown(
        f"""
Scores are built from orders **before** {meta.get('cutoff_date', 'the cutoff')}
(the feature window). Labels come **only** from orders after it (the outcome
window). No feature can see the outcome it is being judged against.
        """
    )

    if metrics_df.empty:
        st.info("Run the pipeline to produce evaluation metrics.")
        return

    import json
    metrics = {}
    for _, r in metrics_df.iterrows():
        try:
            metrics[r["metric"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            metrics[r["metric"]] = r["value"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Precision@5", fmt_num(metrics.get("precision_at_5"), 3))
    c2.metric("Precision@10", fmt_num(metrics.get("precision_at_10"), 3))
    c3.metric("Precision@20", fmt_num(metrics.get("precision_at_20"), 3))
    c4.metric("Base rate", fmt_num(metrics.get("base_rate"), 3))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Precision", fmt_num(metrics.get("precision"), 3))
    c6.metric("Recall", fmt_num(metrics.get("recall"), 3))
    c7.metric("F1-score", fmt_num(metrics.get("f1"), 3))
    lift = metrics.get("lift_at_10")
    c8.metric("Lift @10", f"{fmt_num(lift, 2)}x")

    st.dataframe(evaluation.metrics_to_frame(metrics),
                 use_container_width=True, hide_index=True)

    if not detail.empty and "label" in detail.columns:
        st.markdown("#### Precision as the audit list grows")
        detail = detail.sort_values("risk_score", ascending=False).reset_index(drop=True)
        ks = list(range(1, min(len(detail), 50) + 1))
        curve = pd.DataFrame(
            {"k": ks,
             "precision_at_k": [detail["label"].head(k).mean() for k in ks]}
        )
        base = float(detail["label"].mean())
        fig = px.line(curve, x="k", y="precision_at_k", template=PLOT_TEMPLATE,
                      color_discrete_sequence=["#38bdf8"])
        fig.add_hline(y=base, line_dash="dash", line_color="#94a3b8",
                      annotation_text="random baseline")
        fig.update_layout(height=360, xaxis_title="Sellers audited (k)",
                          yaxis_title="Precision@k")
        st.plotly_chart(fig, use_container_width=True)


def page_stress_test(data):
    st.subheader("Judge Stress Test")
    st.markdown(
        """
This builds a fresh population of **normal sellers + artificially degraded
sellers**, pushes them through the *same* scoring path as production
(Isolation Forest -> ghost score -> risk score), and reports how many degraded
sellers were caught.

The injected sellers exist only in memory. Nothing on disk is modified, so you
can run this as many times as you like.
        """
    )

    sellers = data["seller_features"]
    if sellers.empty:
        st.info("Run the pipeline first.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        n_normal = st.number_input("Normal sellers", 5, 500,
                                   int(config.STRESS_TEST_CONFIG["n_normal"]), step=5)
    with c2:
        n_degraded = st.number_input("Degraded sellers", 1, 200,
                                     int(config.STRESS_TEST_CONFIG["n_degraded"]), step=1)
    with c3:
        threshold = st.slider("Detection threshold (risk score)", 0, 100,
                              int(config.HIGH_RISK_THRESHOLD), step=1)

    with st.expander("Injected profiles"):
        st.json(
            {
                "normal": config.STRESS_TEST_CONFIG["normal_profile"],
                "degraded": config.STRESS_TEST_CONFIG["degraded_profile"],
            }
        )

    if st.button("Run Judge Stress Test", type="primary", use_container_width=True):
        bundle = anomaly_detection.load_model()
        if bundle is None:
            st.warning("No trained model found - scoring on rules only. "
                       "Run `python scripts/run_pipeline.py` for the full path.")
        settings = {
            "n_normal": int(n_normal),
            "n_degraded": int(n_degraded),
            "detection_threshold": int(threshold),
            "random_seed": int(np.random.randint(0, 10_000)),
        }
        try:
            summary, detail = stress_mod.run_stress_test(sellers, bundle, settings)
        except Exception as exc:                    # noqa: BLE001
            st.error(f"Stress test failed: {exc}")
            return

        database.save_stress_test(summary, detail)
        st.session_state["stress_summary"] = summary
        st.session_state["stress_detail"] = detail

    summary = st.session_state.get("stress_summary")
    detail = st.session_state.get("stress_detail")

    if summary is None:
        stored = data.get("stress_test_results", pd.DataFrame())
        if not stored.empty:
            summary = stored.iloc[-1].to_dict()
            detail = data.get("stress_test_detail", pd.DataFrame())

    if summary is None:
        st.info("Press the button to run the test.")
        return

    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Normal sellers", f"{int(summary['n_normal']):,}")
    c2.metric("Degraded sellers", f"{int(summary['n_degraded']):,}")
    c3.metric("Detected", f"{int(summary['n_detected']):,}")
    c4.metric("Missed", f"{int(summary['n_missed']):,}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Detection rate", fmt_pct(summary["detection_rate"]))
    c6.metric("Precision", fmt_num(summary["precision"], 3))
    c7.metric("Recall", fmt_num(summary["recall"], 3))
    c8.metric("False alarms", f"{int(summary['false_alarms']):,}")

    detected = int(summary["n_detected"])
    total = int(summary["n_degraded"])
    if total and detected == total:
        st.success(f"PASS - all {total} degraded sellers were flagged before "
                   f"any simulated order failure.")
    elif total:
        st.warning(f"{detected}/{total} degraded sellers detected. "
                   f"Missed: {int(summary['n_missed'])}.")

    c1, c2 = st.columns(2)
    c1.metric("Avg risk - degraded", fmt_num(summary.get("avg_risk_degraded")))
    c2.metric("Avg risk - normal", fmt_num(summary.get("avg_risk_normal")))

    if detail is not None and not detail.empty:
        st.markdown("#### Score separation")
        fig = px.box(detail, x="injected_type", y="risk_score",
                     color="injected_type", template=PLOT_TEMPLATE,
                     points="all",
                     color_discrete_map={"NORMAL": "#22c55e", "DEGRADED": "#ef4444"})
        fig.add_hline(y=float(summary["threshold"]), line_dash="dash",
                      line_color="#f97316", annotation_text="detection threshold")
        fig.update_layout(height=380, xaxis_title="", yaxis_title="Risk score",
                          showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Detected degraded sellers")
        degraded = detail[detail["injected_type"] == "DEGRADED"] \
            .sort_values("risk_score", ascending=False)
        st.dataframe(style_risk_table(degraded, "risk_score"),
                     use_container_width=True, hide_index=True, height=340)

        missed = degraded[degraded["detected"] == 0]
        if not missed.empty:
            st.markdown("#### Missed degraded sellers")
            st.dataframe(missed, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# ENTRY POINT - called by dashboard/pages/5_Admin_Center.py
# ---------------------------------------------------------------------------
NAV_OPTIONS = [
    "Overview", "Seller Risk", "Product Risk", "Ghost Listings",
    "Seller Details", "Analytics", "Evaluation", "Judge Stress Test",
]


def render_risk_admin():
    """
    Render the risk-intelligence section inside the Admin Center. Assumes
    the caller has already verified the viewer is an authenticated ADMIN
    and has already called st.set_page_config(). Returns nothing; renders
    directly with Streamlit calls, same as the standalone pages/ pattern.
    """
    if not config.DB_PATH.exists():
        empty_state()
        return

    data = load_data(db_signature())
    meta = database.read_meta(data.get("run_meta", pd.DataFrame()))

    if data["seller_features"].empty:
        st.error("The database exists but contains no seller results.")
        empty_state()
        return

    source = meta.get("data_source", "unknown")
    if source == "DEMO":
        st.warning("Risk intelligence is running on **DEMO DATA** (synthetic).")
    else:
        st.success("Risk intelligence is running on the **Olist** dataset.")
    st.caption(
        f"Last pipeline run: {meta.get('run_at', 'unknown')} · "
        f"Feature/outcome cutoff: {str(meta.get('cutoff_date', ''))[:10]}"
    )

    page = st.radio("Risk section", NAV_OPTIONS, horizontal=True, label_visibility="collapsed")

    if st.button("Re-run pipeline", type="secondary"):
        if run_script("scripts/run_pipeline.py"):
            st.cache_data.clear()
            st.rerun()

    st.caption(
        "TrustCatalog reports behavioural risk. It never asserts that a "
        "seller is fraudulent or definitively out of stock."
    )
    st.divider()

    if page == "Overview":
        page_overview(data, meta)
    elif page == "Seller Risk":
        page_seller_risk(data)
    elif page == "Product Risk":
        page_product_risk(data)
    elif page == "Ghost Listings":
        page_ghost_listings(data)
    elif page == "Seller Details":
        page_seller_details(data)
    elif page == "Analytics":
        page_analytics(data)
    elif page == "Evaluation":
        page_evaluation(data, meta)
    elif page == "Judge Stress Test":
        page_stress_test(data)
