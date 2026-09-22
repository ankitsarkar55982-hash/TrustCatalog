"""
Seed the shop with demo accounts and a realistic product catalog.

Run:  python scripts/init_catalog.py

Creates:
  - demo_admin / demo_seller / demo_customer accounts (DEMO ONLY passwords -
    see the printed warning; change before any real deployment)
  - One additional seller account PER real seller in the risk pipeline's
    seller_features table, so TrustCatalog's existing risk scores have a
    real shopping-seller to attach to (this is the integration point
    between the ML pipeline and the marketplace).
  - A small, realistic product catalog distributed across those sellers.

Safe to re-run: skips anything that already exists.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src import database as risk_db
from src import shop_db, security
from scripts import generate_product_images

PRODUCT_CATALOG = [
    # (name, category_slug, price, stock, description, image_filename)
    ("Stainless Steel Chef Knife 8-inch", "home-kitchen", 34.99, 40,
     "Full-tang forged chef knife with a comfortable ergonomic handle.",
     "chef_knife.png"),
    ("Non-Stick Ceramic Frying Pan 28cm", "home-kitchen", 27.50, 25,
     "PFOA-free ceramic coating, induction compatible.",
     "frying_pan.png"),
    ("Wireless Earbuds with Charging Case", "electronics", 49.99, 60,
     "Bluetooth 5.3, 24-hour battery life with the case, touch controls.",
     "wireless_earbuds.png"),
    ("Portable Bluetooth Speaker", "electronics", 39.00, 35,
     "IPX7 waterproof, 12-hour playtime, built-in mic for calls.",
     "bluetooth_speaker.png"),
    ("Adjustable Dumbbell Set 2x10kg", "sports-outdoors", 89.00, 15,
     "Quick-adjust plates, knurled grip, includes storage tray.",
     "dumbbell_set.png"),
    ("Yoga Mat with Carry Strap", "sports-outdoors", 22.99, 50,
     "6mm extra-cushion NBR foam, non-slip texture on both sides.",
     "yoga_mat.png"),
    ("Vitamin C Serum 30ml", "beauty", 18.50, 70,
     "20% Vitamin C with hyaluronic acid, brightening formula.",
     "vitamin_c_serum.png"),
    ("Bamboo Hair Brush Set (3-pack)", "beauty", 14.00, 45,
     "Eco-friendly bamboo handles, gentle on scalp.",
     "hair_brush_set.png"),
    ("Wooden Building Blocks 100pc", "toys-games", 24.99, 30,
     "Non-toxic paint, ages 3+, includes storage bag.",
     "building_blocks.png"),
    ("Strategy Board Game - Trade Routes", "toys-games", 32.00, 20,
     "2-5 players, 45-60 minute playtime, family friendly.",
     "board_game.png"),
    ("The Quiet Algorithm - Novel", "books", 15.99, 55,
     "A near-future thriller about a rogue recommendation engine.",
     "novel_book.png"),
    ("Everyday Sourdough - Cookbook", "books", 21.00, 40,
     "60 step-by-step recipes for home bakers of any level.",
     "cookbook.png"),
    ("Men's Slim-Fit Oxford Shirt", "fashion", 29.99, 65,
     "100% cotton, machine washable, sizes S-XXL.",
     "mens_shirt.png"),
    ("Women's High-Waist Leggings", "fashion", 24.50, 80,
     "Four-way stretch, squat-proof fabric, side pocket.",
     "womens_leggings.png"),
    ("Pruning Shears - Titanium Coated", "garden-tools", 16.99, 38,
     "Non-stick blade, ergonomic grip, safety lock.",
     "pruning_shears.png"),
    ("Garden Hose 15m with Nozzle", "garden-tools", 27.00, 22,
     "Kink-resistant, includes 8-pattern spray nozzle.",
     "garden_hose.png"),
]

DEMO_ACCOUNTS = [
    # username, email, password, role, display_name, shop_name
    ("demo_customer", "demo_customer@example.com", "DemoPass!2026", "CUSTOMER",
     "Demo Customer", None),
    ("demo_seller", "demo_seller@example.com", "DemoPass!2026", "SELLER",
     "Demo Seller", "Demo Seller's Shop"),
    ("demo_admin", "demo_admin@example.com", "DemoPass!2026", "ADMIN",
     "Demo Admin", None),
]


def ensure_demo_accounts(conn):
    created = []
    for username, email, password, role, display_name, shop_name in DEMO_ACCOUNTS:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            continue
        user_id = shop_db.register_user(
            conn, username, email, password, role, display_name, shop_name
        )
        created.append((username, role, user_id))
    return created


def ensure_risk_seller_accounts(conn, max_sellers=15):
    """
    For each real seller already scored by the risk pipeline, create a
    matching shop seller account (if one doesn't exist) so their
    risk_score/risk_level has a real storefront attached to it. Limited
    to `max_sellers` so the demo catalog stays browsable.
    """
    risk_data = risk_db.load_all()
    seller_features = risk_data.get("seller_features")
    if seller_features is None or seller_features.empty:
        print("  [warn] no seller_features found - run scripts/run_pipeline.py first.")
        return {}

    top = seller_features.sort_values("risk_score", ascending=False)
    picked = top.head(max_sellers // 2)  # some risky ones, for the admin demo
    rest = top.tail(max(len(top) - len(picked), 0)).sample(
        n=min(max_sellers - len(picked), max(len(top) - len(picked), 0)),
        random_state=42,
    ) if len(top) > len(picked) else top.iloc[0:0]
    chosen = list(picked["seller_id"]) + list(rest["seller_id"])

    mapping = {}
    for i, risk_seller_id in enumerate(chosen):
        username = f"seller_{risk_seller_id.lower()}"
        row = conn.execute(
            "SELECT user_id FROM seller_profiles WHERE risk_seller_id = ?",
            (risk_seller_id,),
        ).fetchone()
        if row:
            mapping[risk_seller_id] = row["user_id"]
            continue

        existing_user = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing_user:
            user_id = existing_user["id"]
        else:
            user_id = shop_db.register_user(
                conn, username, f"{username}@example-marketplace.demo",
                "DemoPass!2026", "SELLER",
                f"Seller {risk_seller_id}", f"{risk_seller_id} Storefront",
            )
        conn.execute(
            "UPDATE seller_profiles SET risk_seller_id = ? WHERE user_id = ?",
            (risk_seller_id, user_id),
        )
        conn.commit()
        mapping[risk_seller_id] = user_id
    return mapping


def seed_products(conn, seller_user_ids, rng):
    categories = {c["slug"]: c["id"] for c in shop_db.list_categories(conn)}
    existing_count = conn.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]
    if existing_count > 0:
        print(f"  catalog already has {existing_count} products - skipping seed.")
        return 0

    created = 0
    for name, slug, price, stock, description, image_filename in PRODUCT_CATALOG:
        seller_user_id = rng.choice(seller_user_ids)
        shop_db.create_product(
            conn, seller_user_id, name, description, price, stock,
            categories.get(slug), image_filename=image_filename,
        )
        created += 1
    return created


def main():
    print("Generating product images (idempotent, offline)...")
    generate_product_images.main()

    conn = shop_db.get_connection()
    shop_db.init_schema(conn)

    print("Seeding demo accounts...")
    created = ensure_demo_accounts(conn)
    for username, role, user_id in created:
        print(f"  created {role:<9} {username} (id={user_id})")

    print("Linking risk-pipeline sellers to shop accounts...")
    mapping = ensure_risk_seller_accounts(conn)
    print(f"  {len(mapping)} risk sellers linked to shop seller accounts")

    seller_ids = [mapping[k] for k in mapping] or []
    demo_seller_row = conn.execute(
        "SELECT id FROM users WHERE username = 'demo_seller'"
    ).fetchone()
    if demo_seller_row:
        seller_ids.append(demo_seller_row["id"])

    if not seller_ids:
        print("No seller accounts available - cannot seed products. "
              "Run scripts/run_pipeline.py first, or register a seller manually.")
        return

    print("Seeding product catalog...")
    rng = random.Random(42)
    n = seed_products(conn, seller_ids, rng)
    print(f"  {n} products created")

    print("Syncing risk alerts from the pipeline's latest run...")
    n_alerts = shop_db.sync_risk_alerts(conn)
    print(f"  {n_alerts} new alert(s) created for HIGH/CRITICAL risk sellers")

    print("\nDEMO ACCOUNTS (DEMO ONLY - change before any real deployment):")
    for username, email, password, role, *_ in DEMO_ACCOUNTS:
        print(f"  {role:<9} username={username:<14} password={password}")

    print("\nNext step:  streamlit run dashboard/app.py")
    conn.close()


if __name__ == "__main__":
    main()
