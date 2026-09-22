"""
Seller Center - a seller only ever sees and edits their OWN products and
order items. Every write in src/shop_db.py used here is scoped by
seller_user_id in its SQL WHERE clause, so this is enforced by the
database layer, not merely by hiding buttons in this UI.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from src import database as risk_db
from src import images as img_mod
from src import security, session, shop_db

st.set_page_config(page_title="Seller Center - TrustCatalog", page_icon="🏪", layout="wide")

conn = session.get_connection()
session.sidebar_account_box(conn)
user = session.require_role("SELLER")

st.title(f"🏪 Seller Center — {user['display_name']}")

TABS = st.tabs(["Dashboard", "Products", "Orders", "Trust & Risk"])


def render_product_thumb(product, height_px=140):
    """Same fallback contract as the Shop page: a real image if one safely
    resolves (local file preferred, then a validated URL), otherwise a
    clean 'Image unavailable' placeholder - never an emoji, never broken."""
    source, _alt = img_mod.resolve_product_display(product)
    if source is not None:
        st.image(str(source), use_container_width=True)
    else:
        st.markdown(
            f"<div style='background:rgba(148,163,184,.07);border:1px dashed "
            f"rgba(148,163,184,.35);border-radius:8px;height:{height_px}px;"
            f"display:flex;align-items:center;justify-content:center;"
            f"color:#94a3b8;font-size:{max(height_px // 10, 12)}px;'>Image unavailable</div>",
            unsafe_allow_html=True,
        )


KEEP_CURRENT = "__KEEP_CURRENT__"
NO_IMAGE = "(no image)"
KEEP_LABEL = "(keep current image)"


def image_picker(key_prefix, current_filename=None, current_url=None):
    """
    Shared image-selection widget used by both the add-product and
    edit-product forms. Three ways to set an image, tried in this order:
    an uploaded file, a pasted URL, or a picked demo image. Returns
    (uploaded_file_or_None, chosen_demo_value, url_text) for
    resolve_image_choice() to turn into final field values.
    """
    demo_images = img_mod.list_available_demo_images()
    options = []
    if current_filename or current_url:
        options.append(KEEP_LABEL)
    options.append(NO_IMAGE)
    options.extend(demo_images)
    if current_filename and current_filename not in demo_images:
        options.append(current_filename)

    chosen_label = st.selectbox(
        "Use a demo image", options, index=0,
        key=f"{key_prefix}_demo_img",
        help="Pick from the project's built-in demo product images.",
    )
    url_text = st.text_input(
        "...or paste an image URL (https://...)",
        value="" if chosen_label == KEEP_LABEL else "",
        key=f"{key_prefix}_url",
        help="The image loads directly in the visitor's browser - this app "
             "never downloads or fetches the URL itself.",
    )
    uploaded = st.file_uploader(
        "...or upload your own image (PNG/JPG/WEBP, max 5MB)",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"{key_prefix}_upload",
    )

    if chosen_label == KEEP_LABEL:
        chosen_value = KEEP_CURRENT
    elif chosen_label == NO_IMAGE:
        chosen_value = None
    else:
        chosen_value = chosen_label
    return uploaded, chosen_value, url_text


def resolve_image_choice(uploaded_file, chosen_value, url_text):
    """
    Turn what image_picker() returned into the field values to pass into
    create_product()/update_product(). Priority: an uploaded file wins,
    then a pasted URL, then the demo-image dropdown. Returns a dict with
    whichever of image_filename/image_url should be SET - if the seller
    chose "keep current image" and typed no URL and uploaded nothing, an
    empty dict is returned so callers can skip those fields entirely and
    leave the existing values untouched. Raises security.ValidationError
    on an invalid upload or an invalid URL.
    """
    if uploaded_file is not None:
        data = uploaded_file.getvalue()
        saved_name = img_mod.save_uploaded_product_image(uploaded_file.name, data)
        return {"image_filename": saved_name, "image_url": None}

    if url_text and url_text.strip():
        validated = security.clean_image_url(url_text)  # raises on invalid
        return {"image_filename": None, "image_url": validated}

    if chosen_value == KEEP_CURRENT:
        return {}
    return {"image_filename": chosen_value, "image_url": None}

with session.friendly_errors():
    products = shop_db.list_seller_products(conn, user["id"])
    order_items = shop_db.list_orders_for_seller(conn, user["id"])

    # ---------------------------------------------------------- Dashboard
    with TABS[0]:
        total_sales = sum(i["subtotal"] for i in order_items
                          if i["order_status"] != "CANCELLED")
        pending = sum(1 for i in order_items if i["order_status"] in
                     ("PLACED", "CONFIRMED", "PROCESSING"))
        active_products = sum(1 for p in products if p["is_active"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Products", len(products))
        c2.metric("Active products", active_products)
        c3.metric("Orders received", len({i["order_id"] for i in order_items}))
        c4.metric("Demo sales total", f"${total_sales:.2f}")

        low_stock = [p for p in products if p["is_active"] and 0 < p["stock"] <= 5]
        out_of_stock = [p for p in products if p["is_active"] and p["stock"] == 0]
        if out_of_stock:
            st.warning(
                f"{len(out_of_stock)} active listing(s) are out of stock: "
                + ", ".join(security.escape_html(p["name"]) for p in out_of_stock[:5])
            )
        if low_stock:
            st.info(
                f"{len(low_stock)} listing(s) have 5 or fewer units left: "
                + ", ".join(security.escape_html(p["name"]) for p in low_stock[:5])
            )

        if order_items:
            df = pd.DataFrame(order_items)
            df["order_created_at"] = pd.to_datetime(df["order_created_at"], errors="coerce")
            df["date"] = df["order_created_at"].dt.date
            by_day = df.groupby("date", as_index=False)["subtotal"].sum()
            fig = px.bar(by_day, x="date", y="subtotal", template="plotly_dark",
                        color_discrete_sequence=["#38bdf8"])
            fig.update_layout(height=300, yaxis_title="Demo sales ($)", xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No orders yet - sales activity will appear here.")

    # ---------------------------------------------------------- Products
    with TABS[1]:
        st.markdown("#### Add a product")
        categories = shop_db.list_categories(conn)
        cat_options = {c["name"]: c["id"] for c in categories}
        with st.form("add_product_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                name = st.text_input("Product name")
                price = st.number_input("Price ($)", min_value=0.0, step=1.0)
                category_name = st.selectbox("Category", list(cat_options.keys()))
            with c2:
                stock = st.number_input("Initial stock", min_value=0, step=1)
                description = st.text_area("Description")
            st.markdown("**Product image**")
            add_uploaded, add_choice, add_url = image_picker("add")
            submitted = st.form_submit_button("Add product", type="primary")
        if submitted:
            try:
                image_fields = resolve_image_choice(add_uploaded, add_choice, add_url)
                shop_db.create_product(
                    conn, user["id"], name, description, price, stock,
                    cat_options.get(category_name), **image_fields,
                )
                st.success(f"'{name}' added.")
                st.rerun()
            except security.ValidationError as exc:
                st.error(str(exc))

        st.divider()
        st.markdown("#### Your products")
        if not products:
            st.info("You haven't listed any products yet.")
        for p in products:
            with st.expander(
                f"{'🟢' if p['is_active'] else '⚪'} {p['name']} — "
                f"${p['price']:.2f} · stock {p['stock']}"
            ):
                render_product_thumb(p, height_px=120)
                with st.form(f"edit_{p['id']}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        new_price = st.number_input(
                            "Price ($)", min_value=0.0, value=float(p["price"]),
                            key=f"price_{p['id']}",
                        )
                        new_stock = st.number_input(
                            "Stock", min_value=0, value=int(p["stock"]),
                            key=f"stock_{p['id']}",
                        )
                    with c2:
                        new_desc = st.text_area(
                            "Description", value=p["description"] or "",
                            key=f"desc_{p['id']}",
                        )
                        new_active = st.checkbox(
                            "Listing active", value=bool(p["is_active"]),
                            key=f"active_{p['id']}",
                        )
                    st.markdown("**Product image**")
                    edit_uploaded, edit_choice, edit_url = image_picker(
                        f"edit_{p['id']}",
                        current_filename=p.get("image_filename"),
                        current_url=p.get("image_url"),
                    )
                    save = st.form_submit_button("Save changes")
                if save:
                    try:
                        update_fields = dict(
                            price=new_price, stock=new_stock,
                            description=new_desc, is_active=new_active,
                        )
                        update_fields.update(
                            resolve_image_choice(edit_uploaded, edit_choice, edit_url)
                        )
                        shop_db.update_product(
                            conn, p["id"], user["id"], **update_fields,
                        )
                        st.success("Saved.")
                        st.rerun()
                    except security.ValidationError as exc:
                        st.error(str(exc))

    # ---------------------------------------------------------- Orders
    with TABS[2]:
        st.markdown("#### Orders containing your products")
        if not order_items:
            st.info("No orders yet.")
        else:
            by_order = {}
            for item in order_items:
                by_order.setdefault(item["order_id"], []).append(item)

            transitions = {
                "PLACED": "CONFIRMED", "CONFIRMED": "PROCESSING",
                "PROCESSING": "SHIPPED", "SHIPPED": "OUT_FOR_DELIVERY",
                "OUT_FOR_DELIVERY": "DELIVERED",
            }
            for order_id, order_line_items in by_order.items():
                status = order_line_items[0]["order_status"]
                with st.container(border=True):
                    st.markdown(f"**Order #{order_id}** — {status.replace('_', ' ').title()}")
                    st.caption(order_line_items[0]["order_created_at"][:19].replace("T", " "))
                    for item in order_line_items:
                        st.write(
                            f"- {security.escape_html(item['product_name'])} "
                            f"× {item['quantity']} — ${item['subtotal']:.2f}"
                        )
                    next_status = transitions.get(status)
                    c1, c2 = st.columns(2)
                    with c1:
                        if next_status and st.button(
                            f"Mark as {next_status.replace('_', ' ').title()}",
                            key=f"advance_{order_id}",
                        ):
                            shop_db.update_order_status(conn, order_id, user["id"], next_status)
                            st.rerun()
                    with c2:
                        if status not in ("DELIVERED", "CANCELLED") and st.button(
                            "Cancel order", key=f"cancel_{order_id}"
                        ):
                            shop_db.update_order_status(conn, order_id, user["id"], "CANCELLED")
                            st.rerun()

    # ---------------------------------------------------------- Trust & Risk
    with TABS[3]:
        profile = shop_db.get_seller_profile(conn, user["id"])
        risk_seller_id = profile.get("risk_seller_id") if profile else None

        if not risk_seller_id:
            st.info(
                "This seller account is not yet linked to a TrustCatalog risk "
                "profile. Risk scoring runs on sellers processed by the ML "
                "pipeline (see `scripts/run_pipeline.py` and "
                "`scripts/init_catalog.py`)."
            )
        else:
            risk_data = risk_db.load_all()
            sf = risk_data.get("seller_features")
            row = None
            if sf is not None and not sf.empty:
                match = sf[sf["seller_id"] == risk_seller_id]
                if not match.empty:
                    row = match.iloc[0]

            if row is None:
                st.info("No risk score recorded yet for this seller.")
            else:
                score = float(row["risk_score"])
                level = row["risk_level"]
                icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(level, "⚪")

                st.markdown(f"### {icon} Trust Status: {level}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Risk Score", f"{score:.0f} / 100")
                c2.metric("Cancellation rate", f"{row['cancellation_rate']*100:.1f}%")
                c3.metric("Late delivery rate", f"{row['late_delivery_rate']*100:.1f}%")

                if level in ("HIGH", "CRITICAL"):
                    st.warning(
                        "Elevated risk indicators detected on this account. "
                        "This is decision support for the TrustCatalog review "
                        "team, not a penalty - no automatic action has been "
                        "taken against your account."
                    )
                else:
                    st.success("No major anomalies detected.")

                reasons_df = risk_data.get("risk_explanations")
                if reasons_df is not None and not reasons_df.empty:
                    match = reasons_df[reasons_df["seller_id"] == risk_seller_id]
                    if not match.empty:
                        st.markdown("**Why this score?**")
                        st.markdown(match.iloc[0]["reasons"])

                history = shop_db.get_risk_history(conn, "seller", risk_seller_id)
                if len(history) >= 2:
                    hdf = pd.DataFrame(history)
                    fig = px.line(hdf, x="recorded_at", y="risk_score",
                                 template="plotly_dark",
                                 color_discrete_sequence=["#f97316"])
                    fig.update_layout(height=280, yaxis_title="Risk score",
                                      xaxis_title="", yaxis_range=[0, 100])
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.caption(
                        "Risk history builds up each time an admin runs a risk "
                        "sync (`sync_risk_alerts`). Only one snapshot exists so "
                        "far, so no trend chart yet - this is real data, not "
                        "fabricated history."
                    )

conn.close()
