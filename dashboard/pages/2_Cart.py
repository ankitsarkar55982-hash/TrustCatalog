"""
Cart - review, adjust quantities, and check out with a simulated demo payment.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src import security, session, shop_db

st.set_page_config(page_title="Cart - TrustCatalog", page_icon="🛒", layout="wide")

conn = session.get_connection()
session.sidebar_account_box(conn)
user = session.require_role("CUSTOMER")

st.title("🛒 Your Cart")

with session.friendly_errors():
    items = shop_db.list_cart(conn, user["id"])

    if not items:
        st.info("Your cart is empty.")
        st.page_link("pages/1_Shop.py", label="Continue shopping", icon="🛒")
    else:
        total = 0.0
        for item in items:
            c1, c2, c3, c4, c5 = st.columns([3, 1.2, 1, 1, 0.8])
            subtotal = item["price"] * item["quantity"]
            total += subtotal
            with c1:
                st.markdown(f"**{security.escape_html(item['name'])}**")
                st.caption(f"${item['price']:.2f} each")
            with c2:
                new_qty = st.number_input(
                    "Qty", min_value=0, max_value=max(item["stock"], item["quantity"]),
                    value=item["quantity"], key=f"qty_{item['id']}", label_visibility="collapsed",
                )
            with c3:
                st.write(f"${subtotal:.2f}")
            with c4:
                if item["stock"] < item["quantity"]:
                    st.caption(f"⚠️ only {item['stock']} left")
            with c5:
                if st.button("Remove", key=f"rm_{item['id']}"):
                    shop_db.remove_from_cart(conn, user["id"], item["id"])
                    st.rerun()
            if new_qty != item["quantity"]:
                shop_db.set_cart_quantity(conn, user["id"], item["id"], int(new_qty))
                st.rerun()

        st.divider()
        st.markdown(f"### Total: ${total:.2f}")

        with st.expander("Checkout", expanded=True):
            st.info("**Demo Payment - No Real Money Is Charged.**")
            with st.form("checkout_form"):
                name = st.text_input("Full name", value=user["display_name"])
                address = st.text_area("Shipping address")
                st.text_input("Card number (demo)", value="4242 4242 4242 4242", disabled=True)
                st.caption("This is a simulated payment field for demo purposes only. "
                          "No card data is transmitted, validated, or stored.")
                place = st.form_submit_button(
                    "Place Demo Order", type="primary", use_container_width=True
                )

            if place:
                try:
                    order_id = shop_db.checkout(conn, user["id"], name, address)
                    st.success(f"Order #{order_id} placed! Payment: DEMO_PAID.")
                    st.page_link("pages/3_My_Orders.py", label="View your orders", icon="📦")
                except shop_db.CheckoutError as exc:
                    st.error(str(exc))
                except security.ValidationError as exc:
                    st.error(str(exc))

conn.close()
