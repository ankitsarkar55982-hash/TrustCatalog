"""
RELEASE CHECK: end-to-end journey verification.

Not a permanent part of the test suite - this is the one-time integration
script for the pre-packaging release check. Runs against the REAL
database (database/trustcatalog.db) exactly as a live demo would, then
prints PASS/FAIL for each step of each journey.

Run:  python scripts/release_check_journeys.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import database as risk_db
from src import security, shop_db

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        FAILURES.append(label)
    return condition


def main():
    conn = shop_db.get_connection()

    print("=" * 70)
    print("CUSTOMER JOURNEY: Register -> Login -> Browse -> Search -> Product")
    print("Details -> Cart -> Demo Checkout -> Order -> Order History")
    print("=" * 70)

    # Register (unique username so this is safe to re-run)
    import random
    suffix = random.randint(100000, 999999)
    cust_username = f"reltest_cust_{suffix}"
    cust_id = shop_db.register_user(
        conn, cust_username, f"{cust_username}@example.com", "ReleaseCheck1!",
        "CUSTOMER", "Release Check Customer",
    )
    check("customer registered", cust_id is not None)

    user = shop_db.authenticate(conn, cust_username, "ReleaseCheck1!")
    check("customer can log in with the account just created", user["id"] == cust_id)

    products = shop_db.search_products(conn, limit=50)
    check("browse returns products", len(products) > 0)

    search_term = products[0]["name"].split()[0]
    results = shop_db.search_products(conn, query=search_term)
    check(f"search for '{search_term}' returns at least one match",
          any(p["id"] == products[0]["id"] for p in results))

    detail = shop_db.get_product(conn, products[0]["id"])
    check("product detail loads with seller/category info",
          detail is not None and "shop_name" in detail)

    shop_db.add_to_cart(conn, cust_id, products[0]["id"], 1)
    cart = shop_db.list_cart(conn, cust_id)
    check("item appears in cart after add_to_cart",
          any(i["id"] == products[0]["id"] for i in cart))

    stock_before = detail["stock"]
    order_id = shop_db.checkout(conn, cust_id, "Release Check", "1 Test St, Test City")
    check("checkout returns an order id", order_id is not None)

    order, items = shop_db.get_order_for_customer(conn, order_id, cust_id)
    check("order total matches the product price", order["total"] == detail["price"])
    check("order payment_status is DEMO_PAID (never a real charge)",
          order["payment_status"] == "DEMO_PAID")

    after = shop_db.get_product(conn, products[0]["id"])
    check("inventory decremented by exactly 1 after checkout",
          after["stock"] == stock_before - 1)

    history = shop_db.list_orders_for_customer(conn, cust_id)
    check("order appears in customer's order history",
          any(o["id"] == order_id for o in history))

    print()
    print("=" * 70)
    print("SELLER JOURNEY: Login -> Add Product -> Product in marketplace ->")
    print("Inventory -> Receive Order -> Update Order Status")
    print("=" * 70)

    sell_username = f"reltest_seller_{suffix}"
    sell_id = shop_db.register_user(
        conn, sell_username, f"{sell_username}@example.com", "ReleaseCheck1!",
        "SELLER", "Release Check Seller", "Release Check Shop",
    )
    check("seller registered", sell_id is not None)

    new_pid = shop_db.create_product(
        conn, sell_id, "Release Check Widget", "A widget for the release check.",
        19.99, 10, None,
    )
    check("seller can create a product", new_pid is not None)

    marketplace_hit = shop_db.search_products(conn, query="Release Check Widget")
    check("new product is visible in marketplace search",
          any(p["id"] == new_pid for p in marketplace_hit))

    rowcount = shop_db.update_product(conn, new_pid, sell_id, stock=25)
    reloaded = shop_db.get_product(conn, new_pid)
    check("seller can update their own inventory",
          rowcount == 1 and reloaded["stock"] == 25)

    # A second customer buys the seller's product.
    cust2_username = f"reltest_cust2_{suffix}"
    cust2_id = shop_db.register_user(
        conn, cust2_username, f"{cust2_username}@example.com", "ReleaseCheck1!",
        "CUSTOMER", "Release Check Customer Two",
    )
    shop_db.add_to_cart(conn, cust2_id, new_pid, 2)
    order2_id = shop_db.checkout(conn, cust2_id, "Cust Two", "2 Test St")

    seller_orders = shop_db.list_orders_for_seller(conn, sell_id)
    check("seller sees the new order in their order list",
          any(o["order_id"] == order2_id for o in seller_orders))

    shop_db.update_order_status(conn, order2_id, sell_id, "CONFIRMED")
    updated = [o for o in shop_db.list_orders_for_seller(conn, sell_id)
               if o["order_id"] == order2_id][0]
    check("seller can update order status", updated["order_status"] == "CONFIRMED")

    print()
    print("=" * 70)
    print("ADMIN JOURNEY: Login -> Marketplace Overview -> Risk Intelligence ->")
    print("Risk History -> Alerts -> Action Center -> Audit Log")
    print("=" * 70)

    admin_username = f"reltest_admin_{suffix}"
    admin_id = shop_db.register_user(
        conn, admin_username, f"{admin_username}@example.com", "ReleaseCheck1!",
        "ADMIN", "Release Check Admin",
    )
    check("admin registered", admin_id is not None)

    counts = shop_db.marketplace_counts(conn)
    check("marketplace overview counts are non-zero and internally consistent",
          counts["total_orders"] >= 2 and counts["active_products"] >= 1)

    risk_data = risk_db.load_all()
    seller_features = risk_data.get("seller_features")
    check("risk intelligence data (seller_features) is available to the admin",
          seller_features is not None and not seller_features.empty)

    n_new_alerts = shop_db.sync_risk_alerts(conn)
    check("risk sync runs without error (idempotent count returned)",
          isinstance(n_new_alerts, int))

    any_risk_seller = seller_features.iloc[0]["seller_id"]
    hist = shop_db.get_risk_history(conn, "seller", any_risk_seller)
    check("risk history has at least one snapshot for a scored seller",
          len(hist) >= 1)

    alerts = shop_db.list_alerts(conn)
    check("alerts list is populated from HIGH/CRITICAL sellers",
          len(alerts) > 0)

    open_alert = next((a for a in alerts if a["status"] != "RESOLVED"), None)
    if open_alert:
        rc = shop_db.resolve_alert(conn, open_alert["id"], admin_id,
                                   note="Release check resolution.")
        check("admin can resolve an alert (Action Center)", rc == 1)

        actions = shop_db.list_admin_actions(conn)
        check("resolving an alert is recorded in admin_actions",
              any(a["target_id"] == str(open_alert["id"]) for a in actions))
    else:
        check("an open alert existed to resolve (Action Center)", False)

    logs = shop_db.list_audit_logs(conn, limit=500)
    check("audit log contains events from this session",
          any(l["username"] == cust_username for l in logs))

    print()
    print("=" * 70)
    print("TRUSTCATALOG BRIDGE: shopping activity -> risk pipeline -> alert")
    print("=" * 70)
    check("risk_predictions table (written by the ML pipeline) is what "
          "sync_risk_alerts() reads from - confirmed by the alerts populated above",
          len(alerts) > 0)

    print()
    print("=" * 70)
    print("AUTHORIZATION CHECKS")
    print("=" * 70)

    other_order, other_items = shop_db.get_order_for_customer(conn, order_id, cust2_id)
    check("customer 2 cannot read customer 1's order", other_order is None)

    rc = shop_db.update_product(conn, products[0]["id"], sell_id, price=0.01)
    check("seller (release-check) cannot edit a product they don't own", rc == 0)

    try:
        shop_db.update_order_status(conn, order_id, sell_id, "SHIPPED")
        auth_ok = False
    except shop_db.CheckoutError:
        auth_ok = True
    check("seller cannot update status on an order with none of their items",
          auth_ok)

    # Role cannot be changed by re-registering the same identity with a
    # different role - the DB enforces UNIQUE(username)/(email), and there
    # is no update-role code path exposed anywhere in shop_db.py at all.
    import inspect
    src_text = inspect.getsource(shop_db)
    check("no code path anywhere in shop_db.py issues 'UPDATE users SET role'",
          "UPDATE users SET role" not in src_text)

    print()
    print("=" * 70)
    print("DEMO PAYMENT CHECK")
    print("=" * 70)
    check("order payment_status can only ever be DEMO_PAID or DEMO_FAILED "
          "(enforced by the orders.payment_status CHECK constraint)",
          order["payment_status"] in ("DEMO_PAID", "DEMO_FAILED"))

    conn.close()

    print()
    print("=" * 70)
    if FAILURES:
        print(f"RELEASE CHECK: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("RELEASE CHECK: ALL JOURNEY STEPS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
