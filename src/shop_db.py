"""
Shopping-platform schema and data access layer.

Lives alongside the existing risk-pipeline tables (seller_features,
product_features, risk_predictions, ...) in the SAME SQLite file -
nothing from the existing schema is touched or renamed.

Every query in this file is parameterized ('?' placeholders). There is
no string-formatted SQL anywhere below; that is the SQL-injection
defense, not a WAF or input filter.

Authorization note: this module does not check "is this the right user"
- callers (the Streamlit pages) must pass the acting user's id/role and
this layer filters by it (e.g. list_cart(user_id) only ever returns that
user's rows). Every function that reads or writes user-owned data takes
the owner id as a required parameter for exactly this reason.
"""

import sqlite3
from datetime import datetime, timezone

import config
from src import security

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('CUSTOMER','SELLER','ADMIN')),
    display_name    TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS seller_profiles (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    shop_name       TEXT NOT NULL,
    risk_seller_id  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    slug    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS catalog_products (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id      INTEGER REFERENCES categories(id),
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    price            REAL NOT NULL CHECK (price >= 0),
    stock            INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    image_filename   TEXT,
    image_url        TEXT,
    image_alt_text   TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1,
    risk_product_id  TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalog_products_seller ON catalog_products(seller_user_id);
CREATE INDEX IF NOT EXISTS idx_catalog_products_category ON catalog_products(category_id);
CREATE INDEX IF NOT EXISTS idx_catalog_products_active ON catalog_products(is_active);

CREATE TABLE IF NOT EXISTS cart_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    added_at    TEXT NOT NULL,
    UNIQUE(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS wishlist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
    added_at    TEXT NOT NULL,
    UNIQUE(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_user_id    INTEGER NOT NULL REFERENCES users(id),
    status              TEXT NOT NULL DEFAULT 'PLACED'
                         CHECK (status IN ('PLACED','CONFIRMED','PROCESSING',
                                           'SHIPPED','OUT_FOR_DELIVERY',
                                           'DELIVERED','CANCELLED')),
    payment_status      TEXT NOT NULL DEFAULT 'DEMO_PAID'
                         CHECK (payment_status IN ('DEMO_PAID','DEMO_FAILED')),
    total               REAL NOT NULL CHECK (total >= 0),
    shipping_name       TEXT NOT NULL,
    shipping_address    TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_user_id);

CREATE TABLE IF NOT EXISTS order_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES catalog_products(id),
    seller_user_id    INTEGER NOT NULL REFERENCES users(id),
    product_name      TEXT NOT NULL,
    unit_price        REAL NOT NULL CHECK (unit_price >= 0),
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    subtotal          REAL NOT NULL CHECK (subtotal >= 0)
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_seller ON order_items(seller_user_id);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type    TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'NEW'
                  CHECK (status IN ('NEW','UNDER_REVIEW','RESOLVED')),
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolved_by   INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_open_entity
    ON alerts(entity_type, entity_id, alert_type)
    WHERE status != 'RESOLVED';

CREATE TABLE IF NOT EXISTS risk_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    risk_score    REAL NOT NULL,
    risk_level    TEXT NOT NULL,
    model_version TEXT,
    recorded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_history_entity ON risk_history(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS admin_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user_id   INTEGER NOT NULL REFERENCES users(id),
    action_type     TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    username      TEXT,
    event_type    TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);
"""

DEFAULT_CATEGORIES = [
    ("Home & Kitchen", "home-kitchen"),
    ("Electronics", "electronics"),
    ("Sports & Outdoors", "sports-outdoors"),
    ("Beauty & Personal Care", "beauty"),
    ("Toys & Games", "toys-games"),
    ("Books", "books"),
    ("Fashion", "fashion"),
    ("Garden & Tools", "garden-tools"),
]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection(path=None):
    path = path or config.DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_add_missing_columns(conn):
    """
    Add columns introduced after a database was first created, without
    touching existing rows or dropping anything. SQLite's CREATE TABLE IF
    NOT EXISTS never alters an already-existing table, so new columns need
    an explicit, idempotent ALTER TABLE here. Safe to call on every startup.
    """
    migrations = {
        "catalog_products": [
            ("image_url", "TEXT"),
            ("image_alt_text", "TEXT"),
        ],
    }
    for table, columns in migrations.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    conn.commit()


def init_schema(conn=None):
    """Create every shop table if missing. Never drops or alters existing data."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate_add_missing_columns(conn)
        cur = conn.execute("SELECT COUNT(*) AS n FROM categories")
        if cur.fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO categories (name, slug) VALUES (?, ?)",
                DEFAULT_CATEGORIES,
            )
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


# ---------------------------------------------------------------------
# Audit logging - called by every security-relevant action below
# ---------------------------------------------------------------------

def log_audit(conn, event_type, user_id=None, username=None, detail=""):
    """
    Record a security/audit event. NEVER pass a password, token, or any
    secret in `detail` - this table is meant to be safe to show an admin.
    """
    conn.execute(
        "INSERT INTO audit_logs (user_id, username, event_type, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, username, event_type, detail, _now()),
    )
    conn.commit()


def list_audit_logs(conn, limit=200):
    cur = conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------
# Users / auth
# ---------------------------------------------------------------------

class AuthError(Exception):
    pass


def register_user(conn, username, email, password, role, display_name, shop_name=None):
    """
    Create a user. Raises security.ValidationError for bad input, or
    AuthError if the username/email is already taken.
    Password is hashed here and ONLY here - the plaintext never reaches
    the database or the audit log.
    """
    username = security.clean_username(username)
    email = security.clean_email(email)
    security.check_password_strength(password)
    display_name = security.clean_text(display_name, "Display name", max_len=100)
    if role not in ("CUSTOMER", "SELLER", "ADMIN"):
        raise security.ValidationError("Invalid role.")

    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? OR email = ?", (username, email)
    ).fetchone()
    if existing:
        raise AuthError("That username or email is already registered.")

    password_hash = security.hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, email, password_hash, role, display_name, "
        "is_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
        (username, email, password_hash, role, display_name, _now()),
    )
    user_id = cur.lastrowid

    if role == "SELLER":
        shop_name = security.clean_text(
            shop_name or f"{display_name}'s Shop", "Shop name", max_len=150
        )
        conn.execute(
            "INSERT INTO seller_profiles (user_id, shop_name, created_at) VALUES (?, ?, ?)",
            (user_id, shop_name, _now()),
        )

    conn.commit()
    log_audit(conn, "USER_REGISTERED", user_id=user_id, username=username,
              detail=f"role={role}")
    return user_id


def authenticate(conn, username_or_email, password):
    """
    Returns the user row (dict) on success. Raises AuthError on any failure.
    Deliberately returns the SAME error message whether the account doesn't
    exist or the password is wrong, so login can't be used to enumerate
    registered usernames.
    """
    username_or_email = security.clean_text(
        username_or_email, "Username", max_len=254, required=True
    ).lower()

    row = conn.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (username_or_email, username_or_email),
    ).fetchone()

    generic_error = "Incorrect username/email or password."
    if row is None:
        log_audit(conn, "LOGIN_FAILED", username=username_or_email, detail="no such user")
        raise AuthError(generic_error)
    if not row["is_active"]:
        log_audit(conn, "LOGIN_FAILED", user_id=row["id"], username=row["username"],
                   detail="account disabled")
        raise AuthError("This account has been disabled.")
    if not security.verify_password(password, row["password_hash"]):
        log_audit(conn, "LOGIN_FAILED", user_id=row["id"], username=row["username"],
                   detail="bad password")
        raise AuthError(generic_error)

    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
    conn.commit()
    log_audit(conn, "LOGIN_SUCCESS", user_id=row["id"], username=row["username"])
    return dict(row)


def get_user(conn, user_id):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_seller_profile(conn, user_id):
    row = conn.execute(
        "SELECT * FROM seller_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------

def list_categories(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY name").fetchall()]


def create_product(conn, seller_user_id, name, description, price, stock,
                    category_id, image_filename=None, image_url=None, image_alt_text=None):
    name = security.clean_text(name, "Product name", max_len=security.MAX_NAME)
    description = security.clean_text(description, "Description", required=False)
    price = security.clean_price(price)
    stock = security.clean_quantity(stock, "Stock")
    image_filename = security.clean_image_filename(image_filename)
    image_url = security.clean_image_url(image_url)
    image_alt_text = security.clean_text(
        image_alt_text, "Image alt text", required=False, max_len=300
    ) or None
    now = _now()
    cur = conn.execute(
        "INSERT INTO catalog_products (seller_user_id, category_id, name, description, "
        "price, stock, image_filename, image_url, image_alt_text, is_active, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (seller_user_id, category_id, name, description, price, stock,
         image_filename, image_url, image_alt_text, now, now),
    )
    conn.commit()
    log_audit(conn, "PRODUCT_CREATED", user_id=seller_user_id,
              detail=f"product_id={cur.lastrowid} name={name!r}")
    return cur.lastrowid


def update_product(conn, product_id, seller_user_id, **fields):
    """
    Update a product. `seller_user_id` MUST be the owner - this is the
    server-side authorization check (section 27): the UPDATE's WHERE
    clause filters on seller_user_id, so a seller can never edit another
    seller's row no matter what product_id they pass in.
    """
    allowed = {"name", "description", "price", "stock", "category_id",
               "image_filename", "image_url", "image_alt_text", "is_active"}
    sets, params = [], []
    if "name" in fields:
        sets.append("name = ?")
        params.append(security.clean_text(fields["name"], "Product name", max_len=security.MAX_NAME))
    if "description" in fields:
        sets.append("description = ?")
        params.append(security.clean_text(fields["description"], "Description", required=False))
    if "price" in fields:
        sets.append("price = ?")
        params.append(security.clean_price(fields["price"]))
    if "stock" in fields:
        sets.append("stock = ?")
        params.append(security.clean_quantity(fields["stock"], "Stock"))
    if "category_id" in fields:
        sets.append("category_id = ?")
        params.append(fields["category_id"])
    if "image_filename" in fields:
        sets.append("image_filename = ?")
        params.append(security.clean_image_filename(fields["image_filename"]))
    if "image_url" in fields:
        sets.append("image_url = ?")
        params.append(security.clean_image_url(fields["image_url"]))
    if "image_alt_text" in fields:
        sets.append("image_alt_text = ?")
        params.append(security.clean_text(
            fields["image_alt_text"], "Image alt text", required=False, max_len=300
        ) or None)
    if "is_active" in fields:
        sets.append("is_active = ?")
        params.append(1 if fields["is_active"] else 0)
    if not sets:
        return 0

    sets.append("updated_at = ?")
    params.append(_now())
    params.extend([product_id, seller_user_id])

    cur = conn.execute(
        f"UPDATE catalog_products SET {', '.join(sets)} "
        f"WHERE id = ? AND seller_user_id = ?",
        params,
    )
    conn.commit()
    if cur.rowcount:
        log_audit(conn, "PRODUCT_UPDATED", user_id=seller_user_id,
                  detail=f"product_id={product_id} fields={sorted(fields.keys())}")
    return cur.rowcount


def get_product(conn, product_id):
    row = conn.execute(
        "SELECT p.*, c.name AS category_name, u.display_name AS seller_name, "
        "sp.shop_name FROM catalog_products p "
        "LEFT JOIN categories c ON c.id = p.category_id "
        "LEFT JOIN users u ON u.id = p.seller_user_id "
        "LEFT JOIN seller_profiles sp ON sp.user_id = p.seller_user_id "
        "WHERE p.id = ?",
        (product_id,),
    ).fetchone()
    return dict(row) if row else None


def search_products(conn, query=None, category_id=None, min_price=None,
                     max_price=None, sort="newest", only_active=True, limit=200):
    sql = (
        "SELECT p.*, c.name AS category_name, sp.shop_name FROM catalog_products p "
        "LEFT JOIN categories c ON c.id = p.category_id "
        "LEFT JOIN seller_profiles sp ON sp.user_id = p.seller_user_id "
        "WHERE 1=1 "
    )
    params = []
    if only_active:
        sql += "AND p.is_active = 1 "
    if query:
        q = security.clean_text(query, "Search", max_len=200)
        sql += "AND (p.name LIKE ? OR p.description LIKE ? OR c.name LIKE ?) "
        like = f"%{q}%"
        params += [like, like, like]
    if category_id:
        sql += "AND p.category_id = ? "
        params.append(category_id)
    if min_price is not None:
        sql += "AND p.price >= ? "
        params.append(security.clean_price(min_price, "Minimum price"))
    if max_price is not None:
        sql += "AND p.price <= ? "
        params.append(security.clean_price(max_price, "Maximum price"))

    order = {
        "newest": "p.created_at DESC",
        "price_low": "p.price ASC",
        "price_high": "p.price DESC",
        "name": "p.name ASC",
    }.get(sort, "p.created_at DESC")
    sql += f"ORDER BY {order} LIMIT ?"
    params.append(limit)

    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_seller_products(conn, seller_user_id):
    rows = conn.execute(
        "SELECT p.*, c.name AS category_name FROM catalog_products p "
        "LEFT JOIN categories c ON c.id = p.category_id "
        "WHERE p.seller_user_id = ? ORDER BY p.created_at DESC",
        (seller_user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------

def add_to_cart(conn, user_id, product_id, quantity=1):
    quantity = security.clean_quantity(quantity, "Quantity", max_qty=1000)
    if quantity == 0:
        return
    product = get_product(conn, product_id)
    if product is None or not product["is_active"]:
        raise security.ValidationError("This product is not available.")

    existing = conn.execute(
        "SELECT * FROM cart_items WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    ).fetchone()
    new_qty = (existing["quantity"] if existing else 0) + quantity
    new_qty = min(new_qty, product["stock"] if product["stock"] > 0 else new_qty)
    if existing:
        conn.execute(
            "UPDATE cart_items SET quantity = ? WHERE id = ?", (new_qty, existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (user_id, product_id, quantity, added_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, product_id, new_qty, _now()),
        )
    conn.commit()


def set_cart_quantity(conn, user_id, product_id, quantity):
    quantity = security.clean_quantity(quantity, "Quantity", max_qty=1000)
    if quantity == 0:
        conn.execute(
            "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
    else:
        product = get_product(conn, product_id)
        if product and product["stock"] > 0:
            quantity = min(quantity, product["stock"])
        conn.execute(
            "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
            (quantity, user_id, product_id),
        )
    conn.commit()


def remove_from_cart(conn, user_id, product_id):
    conn.execute(
        "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    )
    conn.commit()


def list_cart(conn, user_id):
    rows = conn.execute(
        "SELECT ci.id AS cart_item_id, ci.quantity, p.* FROM cart_items ci "
        "JOIN catalog_products p ON p.id = ci.product_id "
        "WHERE ci.user_id = ? ORDER BY ci.added_at",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def clear_cart(conn, user_id):
    conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    conn.commit()


# ---------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------

def toggle_wishlist(conn, user_id, product_id):
    existing = conn.execute(
        "SELECT id FROM wishlist_items WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM wishlist_items WHERE id = ?", (existing["id"],))
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO wishlist_items (user_id, product_id, added_at) VALUES (?, ?, ?)",
        (user_id, product_id, _now()),
    )
    conn.commit()
    return True


def list_wishlist(conn, user_id):
    rows = conn.execute(
        "SELECT p.* FROM wishlist_items w JOIN catalog_products p ON p.id = w.product_id "
        "WHERE w.user_id = ? ORDER BY w.added_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Checkout / orders
# ---------------------------------------------------------------------

class CheckoutError(Exception):
    pass


def checkout(conn, user_id, shipping_name, shipping_address):
    """
    Convert the user's cart into an order. Fully transactional: stock is
    re-validated at commit time, and either every row is written or none
    are (see section 12 - "if checkout fails, avoid partially updating
    the database"). This is a DEMO checkout: payment_status is always
    DEMO_PAID, no real payment processor is contacted, and no real
    financial details are collected or stored anywhere.
    """
    shipping_name = security.clean_text(shipping_name, "Name", max_len=100)
    shipping_address = security.clean_text(shipping_address, "Address", max_len=500)

    cart = list_cart(conn, user_id)
    if not cart:
        raise CheckoutError("Your cart is empty.")

    try:
        conn.execute("BEGIN IMMEDIATE")

        total = 0.0
        line_items = []
        for item in cart:
            fresh = conn.execute(
                "SELECT * FROM catalog_products WHERE id = ? AND is_active = 1",
                (item["id"],),
            ).fetchone()
            if fresh is None:
                raise CheckoutError(f"'{item['name']}' is no longer available.")
            if fresh["stock"] < item["quantity"]:
                raise CheckoutError(
                    f"Only {fresh['stock']} left of '{fresh['name']}' - "
                    f"please update your cart."
                )
            subtotal = round(fresh["price"] * item["quantity"], 2)
            total += subtotal
            line_items.append((fresh, item["quantity"], subtotal))

        now = _now()
        cur = conn.execute(
            "INSERT INTO orders (customer_user_id, status, payment_status, total, "
            "shipping_name, shipping_address, created_at, updated_at) "
            "VALUES (?, 'PLACED', 'DEMO_PAID', ?, ?, ?, ?, ?)",
            (user_id, round(total, 2), shipping_name, shipping_address, now, now),
        )
        order_id = cur.lastrowid

        for product, qty, subtotal in line_items:
            conn.execute(
                "INSERT INTO order_items (order_id, product_id, seller_user_id, "
                "product_name, unit_price, quantity, subtotal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order_id, product["id"], product["seller_user_id"], product["name"],
                 product["price"], qty, subtotal),
            )
            conn.execute(
                "UPDATE catalog_products SET stock = stock - ?, updated_at = ? WHERE id = ?",
                (qty, now, product["id"]),
            )

        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    log_audit(conn, "ORDER_CREATED", user_id=user_id,
              detail=f"order_id={order_id} total={total:.2f}")
    return order_id


def list_orders_for_customer(conn, user_id):
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_order_for_customer(conn, order_id, user_id):
    """Authorization-scoped read: returns None if this order isn't the caller's."""
    order = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND customer_user_id = ?",
        (order_id, user_id),
    ).fetchone()
    if order is None:
        return None, []
    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall()
    return dict(order), [dict(r) for r in items]


def list_orders_for_seller(conn, seller_user_id):
    rows = conn.execute(
        "SELECT oi.*, o.status AS order_status, o.created_at AS order_created_at, "
        "o.id AS order_id, o.shipping_name, o.shipping_address "
        "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        "WHERE oi.seller_user_id = ? ORDER BY o.created_at DESC",
        (seller_user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_order_status(conn, order_id, seller_user_id, new_status):
    """
    A seller may only move the status of orders that include at least one
    of their own items - enforced by the EXISTS check, not by trusting the
    caller. Valid transitions are enforced by the CHECK constraint on the
    column itself.
    """
    valid = {"CONFIRMED", "PROCESSING", "SHIPPED", "OUT_FOR_DELIVERY",
             "DELIVERED", "CANCELLED"}
    if new_status not in valid:
        raise security.ValidationError("Invalid order status.")

    owns_item = conn.execute(
        "SELECT 1 FROM order_items WHERE order_id = ? AND seller_user_id = ?",
        (order_id, seller_user_id),
    ).fetchone()
    if not owns_item:
        raise CheckoutError("You do not have an item in this order.")

    conn.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, _now(), order_id),
    )
    conn.commit()
    log_audit(conn, "ORDER_STATUS_UPDATED", user_id=seller_user_id,
              detail=f"order_id={order_id} new_status={new_status}")


# ---------------------------------------------------------------------
# Risk bridge: read the EXISTING risk_predictions / seller_features tables
# written by scripts/run_pipeline.py and surface them as alerts + history.
# This does not run or retrain any model - it only reads what the pipeline
# already computed.
# ---------------------------------------------------------------------

def sync_risk_alerts(conn, model_version="isolation_forest_v1"):
    """
    Snapshot current seller risk scores into risk_history, and open a new
    alert for any seller whose risk_level is HIGH or CRITICAL and who does
    not already have an unresolved alert of this type. Returns the number
    of new alerts created. Safe to call repeatedly (idempotent thanks to
    the partial unique index on open alerts).
    """
    rows = conn.execute(
        "SELECT seller_id, risk_score, risk_level FROM risk_predictions"
    ).fetchall()
    now = _now()
    created = 0
    for row in rows:
        conn.execute(
            "INSERT INTO risk_history (entity_type, entity_id, risk_score, "
            "risk_level, model_version, recorded_at) VALUES ('seller', ?, ?, ?, ?, ?)",
            (row["seller_id"], row["risk_score"], row["risk_level"], model_version, now),
        )
        if row["risk_level"] in ("HIGH", "CRITICAL"):
            try:
                conn.execute(
                    "INSERT INTO alerts (alert_type, severity, entity_type, entity_id, "
                    "reason, status, created_at) VALUES "
                    "('RISK_THRESHOLD', ?, 'seller', ?, ?, 'NEW', ?)",
                    (row["risk_level"], row["seller_id"],
                     f"Seller risk score is {row['risk_score']:.0f}/100 ({row['risk_level']}). "
                     f"Elevated risk indicators detected - manual review recommended.",
                     now),
                )
                created += 1
            except sqlite3.IntegrityError:
                pass  # an open alert of this type already exists for this seller
    conn.commit()
    return created


def get_risk_history(conn, entity_type, entity_id, limit=90):
    rows = conn.execute(
        "SELECT * FROM risk_history WHERE entity_type = ? AND entity_id = ? "
        "ORDER BY recorded_at DESC LIMIT ?",
        (entity_type, entity_id, limit),
    ).fetchall()
    return [dict(r) for r in rows][::-1]


def list_alerts(conn, status=None, limit=200):
    if status:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_alert(conn, alert_id, admin_user_id, note=""):
    note = security.clean_text(note, "Note", required=False, max_len=1000)
    cur = conn.execute(
        "UPDATE alerts SET status = 'RESOLVED', resolved_at = ?, resolved_by = ? "
        "WHERE id = ? AND status != 'RESOLVED'",
        (_now(), admin_user_id, alert_id),
    )
    conn.commit()
    if cur.rowcount:
        conn.execute(
            "INSERT INTO admin_actions (admin_user_id, action_type, target_type, "
            "target_id, note, created_at) VALUES (?, 'RESOLVE_ALERT', 'alert', ?, ?, ?)",
            (admin_user_id, str(alert_id), note, _now()),
        )
        conn.commit()
        log_audit(conn, "ALERT_RESOLVED", user_id=admin_user_id,
                  detail=f"alert_id={alert_id} note={note!r}")
    return cur.rowcount


def set_alert_status(conn, alert_id, admin_user_id, status):
    if status not in ("NEW", "UNDER_REVIEW", "RESOLVED"):
        raise security.ValidationError("Invalid alert status.")
    if status == "RESOLVED":
        return resolve_alert(conn, alert_id, admin_user_id)
    conn.execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))
    conn.commit()
    conn.execute(
        "INSERT INTO admin_actions (admin_user_id, action_type, target_type, "
        "target_id, note, created_at) VALUES (?, ?, 'alert', ?, '', ?)",
        (admin_user_id, f"SET_STATUS_{status}", str(alert_id), _now()),
    )
    conn.commit()
    return 1


def deactivate_product(conn, product_id, admin_user_id, note=""):
    """Admin-only forced deactivation (e.g. following a resolved investigation)."""
    note = security.clean_text(note, "Note", required=False, max_len=1000)
    conn.execute(
        "UPDATE catalog_products SET is_active = 0, updated_at = ? WHERE id = ?",
        (_now(), product_id),
    )
    conn.commit()
    conn.execute(
        "INSERT INTO admin_actions (admin_user_id, action_type, target_type, "
        "target_id, note, created_at) VALUES (?, 'DEACTIVATE_PRODUCT', 'product', ?, ?, ?)",
        (admin_user_id, str(product_id), note, _now()),
    )
    conn.commit()
    log_audit(conn, "PRODUCT_DEACTIVATED_BY_ADMIN", user_id=admin_user_id,
              detail=f"product_id={product_id} note={note!r}")


def set_account_active(conn, user_id, admin_user_id, is_active, note=""):
    note = security.clean_text(note, "Note", required=False, max_len=1000)
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
    conn.commit()
    action = "ENABLE_ACCOUNT" if is_active else "DISABLE_ACCOUNT"
    conn.execute(
        "INSERT INTO admin_actions (admin_user_id, action_type, target_type, "
        "target_id, note, created_at) VALUES (?, ?, 'user', ?, ?, ?)",
        (admin_user_id, action, str(user_id), note, _now()),
    )
    conn.commit()
    log_audit(conn, action, user_id=admin_user_id, detail=f"target_user_id={user_id}")


def list_admin_actions(conn, limit=200):
    rows = conn.execute(
        "SELECT aa.*, u.username AS admin_username FROM admin_actions aa "
        "JOIN users u ON u.id = aa.admin_user_id ORDER BY aa.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Marketplace-wide counts for the admin overview page
# ---------------------------------------------------------------------

def marketplace_counts(conn):
    def scalar(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    return {
        "total_users": scalar("SELECT COUNT(*) FROM users"),
        "total_customers": scalar("SELECT COUNT(*) FROM users WHERE role='CUSTOMER'"),
        "total_sellers": scalar("SELECT COUNT(*) FROM users WHERE role='SELLER'"),
        "total_products": scalar("SELECT COUNT(*) FROM catalog_products"),
        "active_products": scalar("SELECT COUNT(*) FROM catalog_products WHERE is_active=1"),
        "total_orders": scalar("SELECT COUNT(*) FROM orders"),
        "orders_today": scalar(
            "SELECT COUNT(*) FROM orders WHERE date(created_at) = date('now')"
        ),
        "open_alerts": scalar("SELECT COUNT(*) FROM alerts WHERE status != 'RESOLVED'"),
        "critical_alerts": scalar(
            "SELECT COUNT(*) FROM alerts WHERE status != 'RESOLVED' AND severity='CRITICAL'"
        ),
    }
