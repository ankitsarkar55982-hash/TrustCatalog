"""
Admin Center - TrustCatalog's monitoring + risk intelligence + human-review
surface. Restricted to ADMIN accounts.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

import config
from src import anomaly_detection
from src import database as risk_db
from src import session, shop_db

sys.path.insert(0, str(ROOT / "dashboard"))
import risk_admin  # noqa: E402  (the preserved original risk dashboard)

st.set_page_config(page_title="Admin Center - TrustCatalog", page_icon="🛡️", layout="wide")

conn = session.get_connection()
session.sidebar_account_box(conn)
user = session.require_role("ADMIN")

st.title("🛡️ TrustCatalog Admin Center")

TABS = st.tabs([
    "Overview", "Near Real-Time Monitoring", "Risk Intelligence",
    "Alerts", "Action Center", "Model & Dataset", "Audit Logs",
])

with session.friendly_errors():
    # ---------------------------------------------------------- Overview
    with TABS[0]:
        counts = shop_db.marketplace_counts(conn)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Users", counts["total_users"])
        c2.metric("Sellers", counts["total_sellers"])
        c3.metric("Products (active)", f"{counts['active_products']}/{counts['total_products']}")
        c4.metric("Orders today", counts["orders_today"])

        c5, c6, c7 = st.columns(3)
        c5.metric("Total orders", counts["total_orders"])
        c6.metric("Open alerts", counts["open_alerts"])
        c7.metric("Critical alerts", counts["critical_alerts"])

        if counts["critical_alerts"] > 0:
            st.error(f"{counts['critical_alerts']} CRITICAL alert(s) awaiting review.")

        orders_rows = conn.execute(
            "SELECT date(created_at) AS d, COUNT(*) AS n, SUM(total) AS revenue "
            "FROM orders GROUP BY date(created_at) ORDER BY d"
        ).fetchall()
        if orders_rows:
            df = pd.DataFrame([dict(r) for r in orders_rows])
            c1, c2 = st.columns(2)
            with c1:
                fig = px.bar(df, x="d", y="n", template="plotly_dark",
                            color_discrete_sequence=["#38bdf8"])
                fig.update_layout(height=300, title="Orders per day",
                                  xaxis_title="", yaxis_title="Orders")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.bar(df, x="d", y="revenue", template="plotly_dark",
                            color_discrete_sequence=["#22c55e"])
                fig.update_layout(height=300, title="Demo sales per day",
                                  xaxis_title="", yaxis_title="$ (demo)")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No orders placed yet - charts will populate once shopping activity begins.")

    # ------------------------------------------------ Near Real-Time Monitoring
    with TABS[1]:
        st.caption(
            "**Near Real-Time Monitoring.** This view refreshes when you click "
            "the button below or interact with the page - it is Streamlit "
            "polling the database, not a WebSocket push. That distinction is "
            "stated here deliberately rather than calling this 'true real-time'."
        )
        if st.button("🔄 Refresh now", type="primary"):
            st.rerun()

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### Recent orders")
            rows = conn.execute(
                "SELECT id, total, status, created_at FROM orders "
                "ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            if rows:
                st.dataframe(pd.DataFrame([dict(r) for r in rows]),
                            use_container_width=True, hide_index=True)
            else:
                st.caption("No orders yet.")
        with c2:
            st.markdown("#### Recently listed products")
            rows = conn.execute(
                "SELECT id, name, price, stock, created_at FROM catalog_products "
                "ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            if rows:
                st.dataframe(pd.DataFrame([dict(r) for r in rows]),
                            use_container_width=True, hide_index=True)
            else:
                st.caption("No products yet.")

        st.markdown("#### Recent alerts")
        recent_alerts = shop_db.list_alerts(conn, limit=10)
        if recent_alerts:
            st.dataframe(pd.DataFrame(recent_alerts)[
                ["id", "alert_type", "severity", "entity_id", "status", "created_at"]
            ], use_container_width=True, hide_index=True)
        else:
            st.caption("No alerts yet.")

    # ---------------------------------------------------------- Risk Intelligence
    with TABS[2]:
        st.caption(
            "This section is the original TrustCatalog risk-pipeline dashboard, "
            "unchanged, reused here as the Admin Center's intelligence layer."
        )
        c1, c2 = st.columns([1, 3])
        with c1:
            if st.button("Sync risk alerts from latest pipeline run"):
                n = shop_db.sync_risk_alerts(conn)
                st.success(f"{n} new alert(s) created.")
        risk_admin.render_risk_admin()

    # ---------------------------------------------------------- Alerts
    with TABS[3]:
        status_filter = st.selectbox("Status", ["All", "NEW", "UNDER_REVIEW", "RESOLVED"])
        alerts = shop_db.list_alerts(
            conn, status=None if status_filter == "All" else status_filter
        )
        st.caption(f"{len(alerts)} alert(s)")
        for a in alerts:
            sev_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(
                a["severity"], "⚪"
            )
            with st.container(border=True):
                st.markdown(
                    f"{sev_icon} **{a['severity']} - {a['alert_type']}** "
                    f"on {a['entity_type']} `{a['entity_id']}` · {a['status']}"
                )
                st.write(a["reason"])
                st.caption(a["created_at"])
                if a["status"] != "RESOLVED":
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if st.button("Mark under review", key=f"review_{a['id']}"):
                            shop_db.set_alert_status(conn, a["id"], user["id"], "UNDER_REVIEW")
                            st.rerun()
                    with c2:
                        note = st.text_input("Resolution note", key=f"note_{a['id']}")
                    with c3:
                        if st.button("Resolve", key=f"resolve_{a['id']}"):
                            shop_db.resolve_alert(conn, a["id"], user["id"], note)
                            st.rerun()

    # ---------------------------------------------------------- Action Center
    with TABS[4]:
        st.markdown("#### High-priority items")
        high_priority = [a for a in shop_db.list_alerts(conn) if a["status"] != "RESOLVED"
                         and a["severity"] in ("HIGH", "CRITICAL")]
        if not high_priority:
            st.success("Nothing needs review right now.")
        for a in high_priority:
            with st.container(border=True):
                st.markdown(f"**{a['severity']} ALERT** — seller `{a['entity_id']}`")
                st.write(a["reason"])
                st.caption(
                    "Recommended review: verify recent orders, check shipping "
                    "performance, monitor the next activity window. TrustCatalog "
                    "does not automatically restrict this account."
                )

                seller_row = conn.execute(
                    "SELECT user_id FROM seller_profiles WHERE risk_seller_id = ?",
                    (a["entity_id"],),
                ).fetchone()

                c1, c2, c3 = st.columns(3)
                with c1:
                    if seller_row and st.button(
                        "Disable seller account", key=f"disable_{a['id']}"
                    ):
                        shop_db.set_account_active(
                            conn, seller_row["user_id"], user["id"], False,
                            note=f"Disabled following alert #{a['id']}",
                        )
                        st.success("Seller account disabled and logged in the audit trail.")
                        st.rerun()
                with c2:
                    if st.button("Escalate (keep NEW)", key=f"escalate_{a['id']}"):
                        st.info("Marked for escalation - status left as NEW for senior review.")
                with c3:
                    if st.button("Resolve as false positive", key=f"fp_{a['id']}"):
                        shop_db.resolve_alert(conn, a["id"], user["id"],
                                              note="Reviewed - false positive.")
                        st.rerun()

        st.divider()
        st.markdown("#### Recent admin actions")
        actions = shop_db.list_admin_actions(conn, limit=50)
        if actions:
            st.dataframe(
                pd.DataFrame(actions)[
                    ["created_at", "admin_username", "action_type", "target_type",
                     "target_id", "note"]
                ],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("No admin actions recorded yet.")

    # ---------------------------------------------------------- Model & Dataset
    with TABS[5]:
        st.markdown("#### Model information")
        bundle = anomaly_detection.load_model()
        if bundle is None:
            st.warning("No trained model found. Run `python scripts/run_pipeline.py`.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Model type", "Isolation Forest")
            c2.metric("Version", bundle.get("version", "Not currently recorded."))
            c3.metric("Training rows", bundle.get("n_training_rows", "Not currently recorded."))
            st.caption(f"Features used: {', '.join(bundle.get('feature_names', []))}")

        st.markdown("#### Dataset status")
        risk_data = risk_db.load_all()
        meta = risk_db.read_meta(risk_data.get("run_meta", pd.DataFrame()))
        if not meta:
            st.info("No pipeline run recorded yet.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Data source", meta.get("data_source", "Not currently recorded."))
            c2.metric("Sellers scored", meta.get("n_scored_sellers", "Not currently recorded."))
            c3.metric("Listings scored", meta.get("n_scored_listings", "Not currently recorded."))
            st.caption(
                f"Feature window: {str(meta.get('window_start',''))[:10]} → "
                f"{str(meta.get('cutoff_date',''))[:10]}  ·  "
                f"Outcome window: {str(meta.get('cutoff_date',''))[:10]} → "
                f"{str(meta.get('window_end',''))[:10]}"
            )
            st.caption(f"Last run: {meta.get('run_at', 'Not currently recorded.')}")

        st.markdown("#### Evaluation metrics (from the latest pipeline run)")
        metrics_df = risk_data.get("evaluation_metrics")
        if metrics_df is not None and not metrics_df.empty:
            from src import evaluation as eval_mod
            import json
            metrics = {}
            for _, r in metrics_df.iterrows():
                try:
                    metrics[r["metric"]] = json.loads(r["value"])
                except (TypeError, ValueError):
                    metrics[r["metric"]] = r["value"]
            st.dataframe(eval_mod.metrics_to_frame(metrics), use_container_width=True,
                        hide_index=True)
        else:
            st.caption("Not currently recorded.")

    # ---------------------------------------------------------- Audit Logs
    with TABS[6]:
        logs = shop_db.list_audit_logs(conn, limit=300)
        st.caption(f"{len(logs)} most recent audit events. Passwords are never logged.")
        if logs:
            st.dataframe(
                pd.DataFrame(logs)[["created_at", "event_type", "username", "detail"]],
                use_container_width=True, hide_index=True, height=500,
            )
        else:
            st.caption("No audit events yet.")

conn.close()
