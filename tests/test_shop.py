"""
Tests for the shopping-platform layer (auth, authorization, cart, checkout,
risk-alert bridge). Uses a throwaway SQLite file so it never touches the
real database.

Run:  python tests/test_shop.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import security, shop_db


def fresh_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # sqlite3.connect creates it fresh
    conn = shop_db.get_connection(Path(path))
    shop_db.init_schema(conn)
    return conn, Path(path)


def cleanup(conn, path):
    conn.close()
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------- auth

def test_password_hash_roundtrip():
    h = security.hash_password("CorrectHorseBattery9!")
    assert security.verify_password("CorrectHorseBattery9!", h)
    assert not security.verify_password("wrong-password", h)
    assert h.startswith("pbkdf2_sha256$")
    print("  password hashing round-trips and rejects wrong passwords")


def test_password_not_stored_plaintext():
    conn, path = fresh_conn()
    try:
        shop_db.register_user(conn, "alice", "alice@example.com", "SuperSecret1!",
                              "CUSTOMER", "Alice")
        row = conn.execute("SELECT password_hash FROM users WHERE username='alice'").fetchone()
        assert "SuperSecret1!" not in row["password_hash"]
        assert row["password_hash"].startswith("pbkdf2_sha256$")
    finally:
        cleanup(conn, path)
    print("  plaintext password never touches the database")


def test_register_and_login():
    conn, path = fresh_conn()
    try:
        shop_db.register_user(conn, "bob", "bob@example.com", "GoodPass123!",
                              "CUSTOMER", "Bob")
        user = shop_db.authenticate(conn, "bob", "GoodPass123!")
        assert user["username"] == "bob"
        user2 = shop_db.authenticate(conn, "bob@example.com", "GoodPass123!")
        assert user2["id"] == user["id"]
    finally:
        cleanup(conn, path)
    print("  register + login by username or email works")


def test_login_rejects_wrong_password_and_unknown_user():
    conn, path = fresh_conn()
    try:
        shop_db.register_user(conn, "carol", "carol@example.com", "GoodPass123!",
                              "CUSTOMER", "Carol")
        try:
            shop_db.authenticate(conn, "carol", "wrong-password")
            assert False, "should have raised AuthError"
        except shop_db.AuthError:
            pass
        try:
            shop_db.authenticate(conn, "no_such_user", "whatever")
            assert False, "should have raised AuthError"
        except shop_db.AuthError:
            pass
    finally:
        cleanup(conn, path)
    print("  wrong password and unknown user are both rejected")


def test_duplicate_registration_rejected():
    conn, path = fresh_conn()
    try:
        shop_db.register_user(conn, "dave", "dave@example.com", "GoodPass123!",
                              "CUSTOMER", "Dave")
        try:
            shop_db.register_user(conn, "dave", "other@example.com", "GoodPass123!",
                                  "CUSTOMER", "Dave2")
            assert False, "should have raised AuthError"
        except shop_db.AuthError:
            pass
    finally:
        cleanup(conn, path)
    print("  duplicate username registration is rejected")


def test_disabled_account_cannot_login():
    conn, path = fresh_conn()
    try:
        uid = shop_db.register_user(conn, "eve", "eve@example.com", "GoodPass123!",
                                    "CUSTOMER", "Eve")
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
        conn.commit()
        try:
            shop_db.authenticate(conn, "eve", "GoodPass123!")
            assert False, "should have raised AuthError"
        except shop_db.AuthError:
            pass
    finally:
        cleanup(conn, path)
    print("  disabled account cannot log in")


# ---------------------------------------------------------- input validation

def test_input_validation_rejects_bad_values():
    conn, path = fresh_conn()
    try:
        cases = [
            lambda: shop_db.register_user(conn, "a", "bad-email", "GoodPass123!",
                                          "CUSTOMER", "A"),               # bad email
            lambda: shop_db.register_user(conn, "ab", "ok@example.com", "GoodPass123!",
                                          "CUSTOMER", "A"),               # username too short
            lambda: shop_db.register_user(conn, "okuser", "ok2@example.com", "short",
                                          "CUSTOMER", "A"),               # weak password
            lambda: shop_db.register_user(conn, "okuser2", "ok3@example.com", "GoodPass123!",
                                          "SUPERADMIN", "A"),             # invalid role
        ]
        for case in cases:
            try:
                case()
                assert False, "expected ValidationError"
            except security.ValidationError:
                pass
    finally:
        cleanup(conn, path)
    print("  bad email / short username / weak password / invalid role all rejected")


def test_negative_price_and_quantity_rejected():
    conn, path = fresh_conn()
    try:
        seller = shop_db.register_user(conn, "seller1", "s1@example.com", "GoodPass123!",
                                       "SELLER", "Seller One", "Shop One")
        try:
            shop_db.create_product(conn, seller, "Widget", "desc", -5.00, 10, None)
            assert False, "expected ValidationError for negative price"
        except security.ValidationError:
            pass
        try:
            shop_db.create_product(conn, seller, "Widget", "desc", 5.00, -3, None)
            assert False, "expected ValidationError for negative stock"
        except security.ValidationError:
            pass
    finally:
        cleanup(conn, path)
    print("  negative price and negative stock are rejected")


def test_sql_injection_like_input_is_inert():
    conn, path = fresh_conn()
    try:
        seller = shop_db.register_user(conn, "seller2", "s2@example.com", "GoodPass123!",
                                       "SELLER", "Seller Two", "Shop Two")
        shop_db.create_product(conn, seller, "Normal Widget", "desc", 9.99, 5, None)
        evil = "widget'; DROP TABLE users; --"
        results = shop_db.search_products(conn, query=evil)
        assert results == []
        # users table must still exist and still have our seller in it
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        assert row["n"] >= 1
    finally:
        cleanup(conn, path)
    print("  SQL-injection-shaped search input is treated as inert text")


def test_upload_validation():
    try:
        security.validate_image_upload("evil.exe", b"MZ\x90\x00fakeexe")
        assert False, "expected ValidationError for .exe"
    except security.ValidationError:
        pass
    try:
        security.validate_image_upload("photo.png", b"not-actually-a-png")
        assert False, "expected ValidationError for content mismatch"
    except security.ValidationError:
        pass
    try:
        security.validate_image_upload("../../etc/passwd.png", b"\x89PNG\r\n\x1a\nrest")
    except security.ValidationError:
        assert False, "a valid PNG with a traversal-ish name should still validate " \
                       "(the caller never uses the raw filename as a path)"
    safe = security.validate_image_upload("../../etc/passwd.png", b"\x89PNG\r\n\x1a\nrest")
    assert ".." not in safe and "/" not in safe
    print("  upload validation rejects wrong type/content and returns a safe filename")


# ---------------------------------------------------------------- authorization

def test_seller_cannot_edit_another_sellers_product():
    conn, path = fresh_conn()
    try:
        s1 = shop_db.register_user(conn, "sellA", "sa@example.com", "GoodPass123!",
                                   "SELLER", "Seller A", "Shop A")
        s2 = shop_db.register_user(conn, "sellB", "sb@example.com", "GoodPass123!",
                                   "SELLER", "Seller B", "Shop B")
        pid = shop_db.create_product(conn, s1, "A's Product", "desc", 10.0, 5, None)

        rowcount = shop_db.update_product(conn, pid, s2, price=1.00)
        assert rowcount == 0, "seller B must NOT be able to edit seller A's product"

        product = shop_db.get_product(conn, pid)
        assert product["price"] == 10.0, "price must be unchanged"
    finally:
        cleanup(conn, path)
    print("  a seller cannot edit another seller's product (IDOR check)")


def test_customer_cannot_read_another_customers_order():
    conn, path = fresh_conn()
    try:
        seller = shop_db.register_user(conn, "sellC", "sc@example.com", "GoodPass123!",
                                       "SELLER", "Seller C", "Shop C")
        pid = shop_db.create_product(conn, seller, "Thing", "desc", 20.0, 10, None)

        cust1 = shop_db.register_user(conn, "cust1", "c1@example.com", "GoodPass123!",
                                      "CUSTOMER", "Customer One")
        cust2 = shop_db.register_user(conn, "cust2", "c2@example.com", "GoodPass123!",
                                      "CUSTOMER", "Customer Two")

        shop_db.add_to_cart(conn, cust1, pid, 1)
        order_id = shop_db.checkout(conn, cust1, "Cust One", "1 Main St")

        order, items = shop_db.get_order_for_customer(conn, order_id, cust2)
        assert order is None, "customer 2 must not be able to read customer 1's order"

        order, items = shop_db.get_order_for_customer(conn, order_id, cust1)
        assert order is not None and len(items) == 1
    finally:
        cleanup(conn, path)
    print("  a customer cannot read another customer's order (IDOR check)")


def test_seller_cannot_update_status_on_order_without_their_item():
    conn, path = fresh_conn()
    try:
        s1 = shop_db.register_user(conn, "sellD", "sd@example.com", "GoodPass123!",
                                   "SELLER", "Seller D", "Shop D")
        s2 = shop_db.register_user(conn, "sellE", "se@example.com", "GoodPass123!",
                                   "SELLER", "Seller E", "Shop E")
        pid = shop_db.create_product(conn, s1, "D's Product", "desc", 5.0, 10, None)
        cust = shop_db.register_user(conn, "cust3", "c3@example.com", "GoodPass123!",
                                     "CUSTOMER", "Customer Three")
        shop_db.add_to_cart(conn, cust, pid, 1)
        order_id = shop_db.checkout(conn, cust, "Cust Three", "2 Main St")

        try:
            shop_db.update_order_status(conn, order_id, s2, "SHIPPED")
            assert False, "seller E has no item in this order"
        except shop_db.CheckoutError:
            pass

        shop_db.update_order_status(conn, order_id, s1, "SHIPPED")
        order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        assert order["status"] == "SHIPPED"
    finally:
        cleanup(conn, path)
    print("  a seller cannot update status on an order that isn't theirs")


# ---------------------------------------------------------------- cart / checkout

def test_checkout_prevents_overselling():
    conn, path = fresh_conn()
    try:
        seller = shop_db.register_user(conn, "sellF", "sf@example.com", "GoodPass123!",
                                       "SELLER", "Seller F", "Shop F")
        pid = shop_db.create_product(conn, seller, "Rare Item", "desc", 100.0, 2, None)
        cust = shop_db.register_user(conn, "cust4", "c4@example.com", "GoodPass123!",
                                     "CUSTOMER", "Customer Four")

        shop_db.add_to_cart(conn, cust, pid, 2)
        # A second customer buys both units first.
        cust2 = shop_db.register_user(conn, "cust5", "c5@example.com", "GoodPass123!",
                                      "CUSTOMER", "Customer Five")
        shop_db.add_to_cart(conn, cust2, pid, 2)
        shop_db.checkout(conn, cust2, "Cust Five", "3 Main St")

        product = shop_db.get_product(conn, pid)
        assert product["stock"] == 0

        try:
            shop_db.checkout(conn, cust, "Cust Four", "4 Main St")
            assert False, "checkout should fail - no stock left"
        except shop_db.CheckoutError:
            pass

        orders = shop_db.list_orders_for_customer(conn, cust)
        assert orders == [], "no partial order should have been created"
    finally:
        cleanup(conn, path)
    print("  checkout prevents overselling and leaves no partial order")


def test_checkout_updates_stock_and_clears_cart():
    conn, path = fresh_conn()
    try:
        seller = shop_db.register_user(conn, "sellG", "sg@example.com", "GoodPass123!",
                                       "SELLER", "Seller G", "Shop G")
        pid = shop_db.create_product(conn, seller, "Widget", "desc", 10.0, 5, None)
        cust = shop_db.register_user(conn, "cust6", "c6@example.com", "GoodPass123!",
                                     "CUSTOMER", "Customer Six")
        shop_db.add_to_cart(conn, cust, pid, 3)
        order_id = shop_db.checkout(conn, cust, "Cust Six", "5 Main St")

        product = shop_db.get_product(conn, pid)
        assert product["stock"] == 2
        assert shop_db.list_cart(conn, cust) == []

        order, items = shop_db.get_order_for_customer(conn, order_id, cust)
        assert order["total"] == 30.0
        assert order["payment_status"] == "DEMO_PAID"
    finally:
        cleanup(conn, path)
    print("  checkout reduces stock, clears cart, and totals correctly")


# ---------------------------------------------------------------- risk bridge

def test_risk_alert_sync_is_idempotent():
    conn, path = fresh_conn()
    try:
        conn.execute(
            "CREATE TABLE risk_predictions (seller_id TEXT, risk_score REAL, risk_level TEXT)"
        )
        conn.execute(
            "INSERT INTO risk_predictions VALUES ('S001', 87.0, 'CRITICAL')"
        )
        conn.commit()

        created1 = shop_db.sync_risk_alerts(conn)
        assert created1 == 1
        created2 = shop_db.sync_risk_alerts(conn)
        assert created2 == 0, "must not create a duplicate open alert on re-sync"

        alerts = shop_db.list_alerts(conn)
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "CRITICAL"
        assert "fraudulent" not in alerts[0]["reason"].lower()
        assert "manual review" in alerts[0]["reason"].lower()

        history = shop_db.get_risk_history(conn, "seller", "S001")
        assert len(history) == 2, "one history row per sync call"
    finally:
        cleanup(conn, path)
    print("  risk alert sync is idempotent, careful in wording, and logs history")


def test_alert_resolution_writes_audit_and_admin_action():
    conn, path = fresh_conn()
    try:
        admin = shop_db.register_user(conn, "adm1", "adm1@example.com", "GoodPass123!",
                                      "ADMIN", "Admin One")
        conn.execute(
            "INSERT INTO alerts (alert_type, severity, entity_type, entity_id, reason, "
            "status, created_at) VALUES ('RISK_THRESHOLD','HIGH','seller','S002','x','NEW','now')"
        )
        conn.commit()
        alert_id = conn.execute("SELECT id FROM alerts").fetchone()["id"]

        rowcount = shop_db.resolve_alert(conn, alert_id, admin, note="Reviewed, false positive.")
        assert rowcount == 1

        alert = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        assert alert["status"] == "RESOLVED"
        assert alert["resolved_by"] == admin

        actions = shop_db.list_admin_actions(conn)
        assert len(actions) == 1
        assert actions[0]["action_type"] == "RESOLVE_ALERT"

        logs = shop_db.list_audit_logs(conn)
        assert any(l["event_type"] == "ALERT_RESOLVED" for l in logs)
    finally:
        cleanup(conn, path)
    print("  resolving an alert records an admin action and an audit log entry")


def test_audit_log_never_contains_password():
    conn, path = fresh_conn()
    try:
        shop_db.register_user(conn, "secure1", "secure1@example.com", "MySecretPass1!",
                              "CUSTOMER", "Secure One")
        try:
            shop_db.authenticate(conn, "secure1", "wrong-guess")
        except shop_db.AuthError:
            pass
        logs = shop_db.list_audit_logs(conn)
        blob = " ".join(f"{l['event_type']} {l['detail']}" for l in logs)
        assert "MySecretPass1!" not in blob
        assert "wrong-guess" not in blob
    finally:
        cleanup(conn, path)
    print("  audit log never contains a plaintext password, even from a failed login")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main():
    failed = 0
    print("Running TrustCatalog shop-platform tests\n")
    for test in TESTS:
        name = test.__name__
        try:
            test()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:                     # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} tests passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
