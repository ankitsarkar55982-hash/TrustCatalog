"""
My Orders - order history and status tracking for the logged-in customer.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src import security, session, shop_db

st.set_page_config(page_title="My Orders - TrustCatalog", page_icon="📦", layout="wide")

conn = session.get_connection()
session.sidebar_account_box(conn)
user = session.require_role("CUSTOMER")

st.title("📦 My Orders")

STATUS_STEPS = ["PLACED", "CONFIRMED", "PROCESSING", "SHIPPED",
                "OUT_FOR_DELIVERY", "DELIVERED"]

with session.friendly_errors():
    orders = shop_db.list_orders_for_customer(conn, user["id"])

    if not orders:
        st.info("You haven't placed any orders yet.")
        st.page_link("pages/1_Shop.py", label="Start shopping", icon="🛒")
    else:
        for order in orders:
            with st.container(border=True):
                c1, c2, c3 = st.columns([1.5, 1, 1])
                with c1:
                    st.markdown(f"**Order #{order['id']}**")
                    st.caption(order["created_at"][:19].replace("T", " "))
                with c2:
                    st.markdown(f"**${order['total']:.2f}**")
                with c3:
                    if order["status"] == "CANCELLED":
                        st.error("Cancelled")
                    elif order["status"] == "DELIVERED":
                        st.success("Delivered")
                    else:
                        st.info(order["status"].replace("_", " ").title())

                if order["status"] in STATUS_STEPS:
                    idx = STATUS_STEPS.index(order["status"])
                    st.progress((idx + 1) / len(STATUS_STEPS))

                with st.expander("Order details"):
                    _, items = shop_db.get_order_for_customer(conn, order["id"], user["id"])
                    for item in items:
                        st.write(
                            f"- {security.escape_html(item['product_name'])} "
                            f"× {item['quantity']} — ${item['subtotal']:.2f}"
                        )
                    st.caption(f"Ship to: {security.escape_html(order['shipping_address'])}")
                    st.caption(f"Payment: {order['payment_status']}")

conn.close()
