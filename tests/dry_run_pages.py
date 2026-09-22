"""
Execute every dashboard page's top-level code with a fake Streamlit runtime,
under each role, to catch NameError/AttributeError/unhandled-exception bugs
without needing a real `streamlit run` (this sandbox has no network access
to `pip install streamlit`).

This does NOT verify layout, CSS, or visual rendering - only that the
Python logic in each page executes without crashing, for a real user of
each role, against real seeded data.

Run:  python tests/dry_run_pages.py
"""

import importlib
import runpy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fake_streamlit  # noqa: E402

import config  # noqa: E402
from src import shop_db  # noqa: E402

PAGES = [
    ("dashboard/app.py", None),
    ("dashboard/pages/1_Shop.py", None),
    ("dashboard/pages/1_Shop.py", "CUSTOMER"),
    ("dashboard/pages/2_Cart.py", "CUSTOMER"),
    ("dashboard/pages/3_My_Orders.py", "CUSTOMER"),
    ("dashboard/pages/4_Seller_Center.py", "SELLER"),
    ("dashboard/pages/5_Admin_Center.py", "ADMIN"),
]

# access-control probes: role tries to open a page it should NOT have
NEGATIVE_PAGES = [
    ("dashboard/pages/4_Seller_Center.py", "CUSTOMER"),
    ("dashboard/pages/5_Admin_Center.py", "CUSTOMER"),
    ("dashboard/pages/5_Admin_Center.py", "SELLER"),
]


def get_user_for_role(role):
    if role is None:
        return None
    conn = shop_db.get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE role = ? ORDER BY id LIMIT 1", (role,)
    ).fetchone()
    conn.close()
    if row is None:
        raise RuntimeError(
            f"No {role} account found - run scripts/init_catalog.py first."
        )
    return dict(row)


def run_page(rel_path, role, expect_stop=False, inputs=None, submits=None, clicks=None):
    st = fake_streamlit.install()
    st.session_state.clear()
    user = get_user_for_role(role)
    if user:
        st.session_state["auth_user"] = user
    st._inputs.update(inputs or {})
    st._form_submits.update(submits or {})
    st._button_clicks.update(clicks or set())

    # Clear any cached modules from a previous page run so top-level code
    # re-executes fresh (mirrors a new Streamlit script run), and so every
    # module that did `import streamlit as st` rebinds to the CURRENT fake
    # streamlit instance instead of a stale one from an earlier install().
    for mod_name in list(sys.modules):
        if (
            mod_name in ("risk_admin", "src.session")
            or mod_name.startswith("dashboard")
        ):
            del sys.modules[mod_name]

    # Force src.session to rebind to the CURRENT fake `st` right now, rather
    # than relying on the child page's own `from src import session` to do
    # it as a side effect during runpy.run_path() below. Both approaches are
    # correct in principle, but this explicit re-import removed a flaky,
    # hard-to-reproduce failure observed during development where session
    # state was occasionally read against a stale `st` instance - see
    # README.md "Bugs found and fixed" for the full account.
    import src.session  # noqa: F401

    label = f"{rel_path}  (role={role or 'guest'})"
    try:
        runpy.run_path(str(ROOT / rel_path), run_name="__main__")
        if expect_stop:
            print(f"FAIL  {label}: expected access to be denied, but page ran to completion")
            return False
        print(f"PASS  {label}")
        return True
    except fake_streamlit.StopScript:
        if expect_stop:
            print(f"PASS  {label}: access correctly denied (page called st.stop())")
            return True
        print(f"PASS  {label} (page called st.stop()/rerun mid-flow, which is normal)")
        return True
    except Exception:                                    # noqa: BLE001
        print(f"FAIL  {label}")
        traceback.print_exc()
        return False


def get_sample_product_id():
    conn = shop_db.get_connection()
    row = conn.execute("SELECT id FROM catalog_products WHERE is_active=1 LIMIT 1").fetchone()
    conn.close()
    return row["id"] if row else None


def main():
    if not config.DB_PATH.exists():
        print("No database found - run scripts/run_pipeline.py and "
              "scripts/init_catalog.py first.")
        sys.exit(1)

    print("=== Positive dry-runs (each role opening its own pages) ===\n")
    results = [run_page(path, role) for path, role in PAGES]

    print("\n=== Negative dry-runs (role opening a page it should NOT access) ===\n")
    results += [run_page(path, role, expect_stop=True) for path, role in NEGATIVE_PAGES]

    print("\n=== Interactive-flow dry-runs (real form submissions / button clicks) ===\n")

    # A guest actually logging in with valid demo credentials.
    results.append(run_page(
        "dashboard/app.py", None,
        inputs={"Username or email": "demo_customer", "Password": "DemoPass!2026"},
    ))

    # A logged-in customer viewing a product and clicking Add to cart.
    pid = get_sample_product_id()
    if pid:
        def customer_view_and_add_to_cart():
            st = fake_streamlit.install()
            user = get_user_for_role("CUSTOMER")
            st.session_state["auth_user"] = user
            st.session_state["viewing_product_id"] = pid
            st._button_clicks.add("Add to cart")
            for mod_name in list(sys.modules):
                if mod_name in ("risk_admin", "src.session") or mod_name.startswith("dashboard"):
                    del sys.modules[mod_name]
            import src.session  # noqa: F401  (force rebind - see run_page() comment)
            try:
                runpy.run_path(str(ROOT / "dashboard/pages/1_Shop.py"), run_name="__main__")
                print("PASS  dashboard/pages/1_Shop.py (customer adds real product to cart)")
                return True
            except fake_streamlit.StopScript:
                print("PASS  dashboard/pages/1_Shop.py (customer adds real product to cart)")
                return True
            except Exception:                              # noqa: BLE001
                print("FAIL  dashboard/pages/1_Shop.py (customer adds real product to cart)")
                traceback.print_exc()
                return False
        results.append(customer_view_and_add_to_cart())

        # Verify the cart actually has the item afterward (real DB check, not just "didn't crash").
        conn = shop_db.get_connection()
        cust = get_user_for_role("CUSTOMER")
        cart = shop_db.list_cart(conn, cust["id"])
        ok = any(item["id"] == pid for item in cart)
        print(f"{'PASS' if ok else 'FAIL'}  cart actually contains the added product "
              f"(verified directly against the database)")
        results.append(ok)
        shop_db.clear_cart(conn, cust["id"])  # leave no side effect behind
        conn.close()

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} page dry-runs behaved correctly.")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
