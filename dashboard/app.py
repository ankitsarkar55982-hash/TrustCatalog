"""
TrustCatalog - landing / login / registration page.

Run:  streamlit run dashboard/app.py

This is the entry point. Streamlit's multipage sidebar (dashboard/pages/)
lists Shop, Cart, My Orders, Seller Center and Admin Center; each of those
pages calls src.session.require_login()/require_role() as its very first
action, so this file only needs to handle "who are you" - not authorization
for the other pages.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

import config
from src import security, session, shop_db

st.set_page_config(
    page_title="TrustCatalog",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1100px; }
      .tc-hero-title { font-size: 2.6rem; font-weight: 800; margin-bottom: 0; }
      .tc-hero-sub { color: #94a3b8; font-size: 1.15rem; margin-top: .25rem; }
      .tc-pillar { background: rgba(148,163,184,0.08); border-radius: 12px;
                   padding: 1rem 1.2rem; height: 100%; }
      .tc-badge { display:inline-block; background:#1e293b; color:#e2e8f0;
                  padding: 2px 10px; border-radius: 999px; font-size: .78rem;
                  margin-right: 6px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def hero():
    st.markdown("<div class='tc-hero-title'>🛡️ TrustCatalog</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='tc-hero-sub'>Shop with confidence. Monitor with intelligence.</div>",
        unsafe_allow_html=True,
    )
    st.write("")
    st.markdown(
        "<span class='tc-badge'>Demo Marketplace</span>"
        "<span class='tc-badge'>Synthetic Data</span>"
        "<span class='tc-badge'>Demo Payment Only</span>",
        unsafe_allow_html=True,
    )
    st.write("")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            "<div class='tc-pillar'><b>For Customers</b><br>"
            "Browse a real product catalog, build a cart, and check out with "
            "a simulated demo payment - no real money is ever charged.</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            "<div class='tc-pillar'><b>For Sellers</b><br>"
            "Manage your own products and orders, and see your TrustCatalog "
            "trust status pulled straight from the risk-scoring pipeline.</div>",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            "<div class='tc-pillar'><b>For Admins</b><br>"
            "Near-real-time marketplace monitoring, risk intelligence, "
            "alerts, and a human-review Action Center with a full audit log.</div>",
            unsafe_allow_html=True,
        )
    st.write("")
    st.markdown(
        "TrustCatalog's risk engine flags **behavioural anomalies** - unusual "
        "cancellation rates, shipping delays, sudden inactivity - for human "
        "review. It never automatically bans an account or asserts that "
        "suspicious activity is confirmed fraud."
    )
    st.divider()


def login_form(conn):
    st.subheader("Log in")
    with st.form("login_form"):
        identifier = st.text_input("Username or email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
    if submitted:
        try:
            user = session.login(conn, identifier, password)
            st.success(f"Welcome back, {user['display_name']}.")
            st.rerun()
        except shop_db.AuthError as exc:
            st.error(str(exc))
        except security.ValidationError as exc:
            st.error(str(exc))

    with st.expander("Demo accounts"):
        st.caption("DEMO ONLY - created by `python scripts/init_catalog.py`.")
        st.code(
            "Customer:  demo_customer / DemoPass!2026\n"
            "Seller:    demo_seller   / DemoPass!2026\n"
            "Admin:     demo_admin    / DemoPass!2026",
            language="text",
        )


def register_form(conn):
    st.subheader("Create an account")
    with st.form("register_form"):
        role = st.radio("I am a...", ["Customer", "Seller"], horizontal=True)
        display_name = st.text_input("Display name")
        username = st.text_input("Choose a username (3-32 chars: letters, numbers, . _ -)")
        email = st.text_input("Email")
        shop_name = None
        if role == "Seller":
            shop_name = st.text_input("Shop name")
        password = st.text_input("Password (min 8 characters)", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Create account", type="primary",
                                          use_container_width=True)

    if submitted:
        if password != confirm:
            st.error("Passwords do not match.")
            return
        try:
            shop_db.register_user(
                conn, username, email, password, role.upper(), display_name, shop_name
            )
            st.success("Account created. You can log in now.")
        except (security.ValidationError, shop_db.AuthError) as exc:
            st.error(str(exc))


def guest_catalog_teaser(conn):
    st.subheader("Browse without an account")
    products = shop_db.search_products(conn, limit=6)
    if not products:
        st.info(
            "No products yet. Run `python scripts/init_catalog.py` to seed "
            "demo accounts and a starter catalog."
        )
        return
    cols = st.columns(3)
    for i, p in enumerate(products):
        with cols[i % 3]:
            st.markdown(f"**{security.escape_html(p['name'])}**")
            st.caption(p.get("category_name") or "Uncategorized")
            st.write(f"${p['price']:.2f} · {p['stock']} in stock")
    st.page_link("pages/1_Shop.py", label="Go to the full Shop →", icon="🛒")

@st.cache_resource(show_spinner="Preparing TrustCatalog demo data...")
def ensure_demo_data():
    """
    Initialize the demo accounts/catalog once per Streamlit process.

    The normal session startup creates the database schema, but the demo
    accounts and starter catalog are created by scripts/init_catalog.py.
    This runs that seeding routine only when the deployed database is
    missing the demo admin or starter products.
    """
    conn = shop_db.get_connection()
    try:
        shop_db.init_schema(conn)

        admin_exists = conn.execute(
            """
            SELECT 1
            FROM users
            WHERE username = 'demo_admin'
              AND role = 'ADMIN'
            LIMIT 1
            """
        ).fetchone()

        product_count = conn.execute(
            "SELECT COUNT(*) FROM catalog_products"
        ).fetchone()[0]
    finally:
        conn.close()

    if admin_exists is None or product_count == 0:
        from scripts import init_catalog

        init_catalog.main()

    return True


def main():
    # Streamlit Cloud does not automatically run scripts/init_catalog.py.
    # Seed the demo accounts/catalog when they are missing.
    ensure_demo_data()

    conn = session.get_connection()

    try:
        with session.friendly_errors():
            session.sidebar_account_box(conn)
            hero()

            user = session.current_user()

            if user:
                st.success(
                    f"You are signed in as **{user['display_name']}** "
                    f"({user['role'].title()})."
                )

                c1, c2 = st.columns(2)

                with c1:
                    st.page_link(
                        "pages/1_Shop.py",
                        label="Go shopping",
                        icon="🛒",
                    )

                with c2:
                    if user["role"] == "SELLER":
                        st.page_link(
                            "pages/4_Seller_Center.py",
                            label="Seller Center",
                            icon="🏪",
                        )
                    elif user["role"] == "ADMIN":
                        st.page_link(
                            "pages/5_Admin_Center.py",
                            label="Admin Center",
                            icon="🛡️",
                        )
                    else:
                        st.page_link(
                            "pages/3_My_Orders.py",
                            label="My Orders",
                            icon="📦",
                        )

            else:
                tab1, tab2 = st.tabs(["Log in", "Create account"])

                with tab1:
                    login_form(conn)

                with tab2:
                    register_form(conn)

            st.divider()
            guest_catalog_teaser(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
