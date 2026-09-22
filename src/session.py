"""
Shared login/session helpers for every Streamlit page.

RBAC model (spec section 27): the role stored in st.session_state is set
ONLY inside login()/register() below, both of which read the role back
from the `users` table row that authenticate()/register_user() returned -
never from a URL, query parameter, form field the viewer controls, or
anything else client-supplied. require_role() below is the enforcement
point every protected page calls before rendering anything sensitive.

Streamlit's multipage sidebar lists every page's link regardless of role
(this is a Streamlit UI limitation, not a security hole): the actual
access control is the require_role() check at the top of each page's
script, which runs server-side on every rerun and st.stop()s the script
before any protected content is built. A customer who clicks "Seller
Center" sees a rendered access-denied message, not seller data - the
link being visible never grants access by itself.
"""

import contextlib
import traceback

import streamlit as st

from src import security, shop_db


@contextlib.contextmanager
def friendly_errors():
    """
    Wrap a page's body in this. Any unhandled exception is caught, the full
    traceback goes to the server-side console log (visible to a developer
    running `streamlit run`, never to the browser), and the viewer sees a
    plain, non-technical message instead - section 35's requirement.
    ValidationError / AuthError / CheckoutError are NOT caught here: pages
    handle those themselves and show the specific, useful message.
    """
    try:
        yield
    except (shop_db.AuthError, shop_db.CheckoutError, security.ValidationError):
        raise
    except Exception:                                    # noqa: BLE001
        print("=== Unhandled error in a Streamlit page ===")
        traceback.print_exc()
        st.error("Something went wrong. Please try again.")
        st.stop()


def get_connection():
    """One connection per Streamlit script run, closed by Streamlit's own
    garbage collection at the end of the run - st.cache_resource would keep
    a single writer connection open across sessions/threads, which SQLite
    handles poorly under concurrent Streamlit sessions, so we open fresh
    each run instead."""
    conn = shop_db.get_connection()
    shop_db.init_schema(conn)
    return conn


def current_user():
    """Returns the logged-in user dict, or None if nobody is logged in."""
    return st.session_state.get("auth_user")


def is_logged_in():
    return current_user() is not None


def login(conn, username_or_email, password):
    """Raises shop_db.AuthError on failure. On success, stores the full
    user row (including role) in session state - this is the only place
    session_state['auth_user'] is ever written from a real login."""
    user = shop_db.authenticate(conn, username_or_email, password)
    st.session_state["auth_user"] = user
    return user


def logout():
    for key in ("auth_user",):
        st.session_state.pop(key, None)
    st.session_state["cart_notice"] = None


def require_login():
    """Call at the top of any page that requires SOME logged-in user.
    Stops the script (renders nothing further) if not logged in."""
    if not is_logged_in():
        st.warning("Please log in to continue.")
        st.page_link("app.py", label="Go to login", icon="🔑")
        st.stop()
    return current_user()


def require_role(*roles):
    """
    Call at the top of any page restricted to specific roles, e.g.
    require_role("ADMIN") or require_role("SELLER", "ADMIN"). Stops the
    script and shows a friendly access-denied message otherwise - never a
    traceback, per section 35.
    """
    user = require_login()
    if user["role"] not in roles:
        st.error(
            f"Access denied. This page is restricted to "
            f"{', '.join(r.title() for r in roles)} accounts."
        )
        st.stop()
    return user


def cart_count(conn, user_id):
    if user_id is None:
        return 0
    try:
        items = shop_db.list_cart(conn, user_id)
        return sum(i["quantity"] for i in items)
    except Exception:                                  # noqa: BLE001
        return 0


def sidebar_account_box(conn):
    """Small reusable widget shown at the top of every page's sidebar."""
    user = current_user()
    with st.sidebar:
        st.markdown("### 🛡️ TrustCatalog")
        if user:
            st.caption(f"Signed in as **{user['display_name']}** ({user['role'].title()})")
            if user["role"] == "CUSTOMER":
                n = cart_count(conn, user["id"])
                st.caption(f"🛒 Cart: {n} item{'s' if n != 1 else ''}")
            if st.button("Log out", use_container_width=True):
                logout()
                st.rerun()
        else:
            st.caption("Not signed in.")
