"""
Shop - browsable to guests; add-to-cart/wishlist requires a CUSTOMER login.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src import images as img_mod
from src import security, session, shop_db

st.set_page_config(page_title="Shop - TrustCatalog", page_icon="🛒", layout="wide")


def render_product_image(product, height_px):
    """
    Render a product's image if one safely resolves (a local file is
    always preferred; a validated image_url is used only when no local
    file is set); otherwise render a clean, explicit "Image unavailable"
    placeholder - never an emoji, never a broken-image icon.
    """
    source, alt_text = img_mod.resolve_product_display(product)
    if source is not None:
        st.image(str(source), use_container_width=True, caption=None)
    else:
        st.markdown(
            f"<div style='background:rgba(148,163,184,.07);border:1px dashed "
            f"rgba(148,163,184,.35);border-radius:12px;height:{height_px}px;"
            f"display:flex;align-items:center;justify-content:center;"
            f"color:#94a3b8;font-size:{max(height_px // 12, 12)}px;text-align:center;"
            f"padding:8px;'>Image unavailable</div>",
            unsafe_allow_html=True,
        )


conn = session.get_connection()
session.sidebar_account_box(conn)
user = session.current_user()

st.title("🛒 Shop")

with session.friendly_errors():
    selected_id = st.session_state.get("viewing_product_id")

    if selected_id:
        product = shop_db.get_product(conn, selected_id)
        if product is None:
            st.error("That product no longer exists.")
            st.session_state["viewing_product_id"] = None
        else:
            if st.button("← Back to results"):
                st.session_state["viewing_product_id"] = None
                st.rerun()

            c1, c2 = st.columns([1, 1.4])
            with c1:
                render_product_image(product, height_px=380)
                _src, _ = img_mod.resolve_product_display(product)
                if _src is not None:
                    st.caption(
                        "Demo catalog - generated illustrative product image, "
                        "not real product photography."
                    )
            with c2:
                st.subheader(security.escape_html(product["name"]))
                st.caption(
                    f"{product.get('category_name') or 'Uncategorized'} · "
                    f"Sold by {security.escape_html(product.get('shop_name') or product.get('seller_name') or 'Unknown seller')}"
                )
                st.markdown(f"### ${product['price']:.2f}")
                if product["stock"] > 0:
                    st.success(f"In stock ({product['stock']} available)")
                else:
                    st.error("Out of stock")
                st.write(product["description"] or "No description provided.")

                if user and user["role"] == "CUSTOMER":
                    qty = st.number_input(
                        "Quantity", min_value=1,
                        max_value=max(product["stock"], 1), value=1,
                        disabled=product["stock"] == 0,
                    )
                    c3, c4 = st.columns(2)
                    with c3:
                        if st.button("Add to cart", type="primary",
                                    disabled=product["stock"] == 0,
                                    use_container_width=True):
                            shop_db.add_to_cart(conn, user["id"], product["id"], int(qty))
                            st.success("Added to cart.")
                    with c4:
                        if st.button("♡ Wishlist", use_container_width=True):
                            added = shop_db.toggle_wishlist(conn, user["id"], product["id"])
                            st.success("Added to wishlist." if added else "Removed from wishlist.")
                elif user and user["role"] != "CUSTOMER":
                    st.info("Only customer accounts can purchase products.")
                else:
                    st.info("Log in as a customer to add this to your cart.")

    else:
        categories = shop_db.list_categories(conn)
        cat_options = {"All categories": None}
        cat_options.update({c["name"]: c["id"] for c in categories})

        f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
        with f1:
            query = st.text_input("Search products", placeholder="e.g. yoga mat, knife, serum")
        with f2:
            cat_name = st.selectbox("Category", list(cat_options.keys()))
        with f3:
            sort = st.selectbox(
                "Sort by",
                ["Newest", "Price: low to high", "Price: high to low", "Name"],
            )
        with f4:
            max_price = st.number_input("Max price ($)", min_value=0.0, value=0.0, step=5.0)

        sort_key = {
            "Newest": "newest", "Price: low to high": "price_low",
            "Price: high to low": "price_high", "Name": "name",
        }[sort]

        products = shop_db.search_products(
            conn,
            query=query or None,
            category_id=cat_options[cat_name],
            max_price=max_price if max_price > 0 else None,
            sort=sort_key,
        )

        st.caption(f"{len(products)} product{'s' if len(products) != 1 else ''} found")

        if not products:
            st.info(
                "No products match. Try clearing filters, or run "
                "`python scripts/init_catalog.py` if the catalog is empty."
            )
        else:
            cols = st.columns(4)
            for i, p in enumerate(products):
                with cols[i % 4]:
                    with st.container(border=True):
                        render_product_image(p, height_px=160)
                        st.markdown(f"**{security.escape_html(p['name'])}**")
                        st.caption(p.get("category_name") or "Uncategorized")
                        st.write(f"${p['price']:.2f}")
                        if p["stock"] == 0:
                            st.caption("Out of stock")
                        elif p["stock"] <= 5:
                            st.caption(f"Only {p['stock']} left")
                        if st.button("View", key=f"view_{p['id']}", use_container_width=True):
                            st.session_state["viewing_product_id"] = p["id"]
                            st.rerun()

conn.close()
