"""
Generate a realistic synthetic marketplace dataset in the Olist schema.

Run:  python scripts/generate_demo_data.py

Output: data/demo/*.csv  with exactly the Olist column names, so the rest of
the pipeline cannot tell the difference between demo and real data.

Seller archetypes
-----------------
  normal    (~62%)  low cancellation, fast handover, good reviews
  medium    (~18%)  mediocre but not alarming
  high      (~12%)  consistently bad
  degrading (~5%)   healthy, then deteriorates part-way through the timeline
                    <- these are the sellers the early-warning system must catch
  ghost     (~3%)   listings stay up, orders stop being fulfilled, then silence
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import config

CATEGORIES = [
    "bed_bath_table", "health_beauty", "sports_leisure", "computers_accessories",
    "furniture_decor", "housewares", "watches_gifts", "telephony", "toys",
    "auto", "garden_tools", "cool_stuff",
]

STATES = ["SP", "RJ", "MG", "RS", "PR", "SC", "BA", "GO", "PE", "CE"]

PROFILES = {
    #                  cancel  late  handover  delivery  review  weight
    "normal":    dict(cancel=0.030, late=0.060, handover=1.0, delivery=9.0, review=4.5, weight=0.62),
    "medium":    dict(cancel=0.090, late=0.150, handover=2.5, delivery=13.0, review=3.9, weight=0.18),
    "high":      dict(cancel=0.220, late=0.320, handover=5.0, delivery=19.0, review=2.6, weight=0.12),
    "degrading": dict(cancel=0.035, late=0.070, handover=1.2, delivery=9.5, review=4.4, weight=0.05),
    "ghost":     dict(cancel=0.300, late=0.400, handover=6.5, delivery=22.0, review=2.1, weight=0.03),
}


def _make_id(prefix, index):
    return f"{prefix}{index:06d}"


def generate(cfg=None, out_dir=None, verbose=True):
    cfg = {**config.DEMO_CONFIG, **(cfg or {})}
    out_dir = Path(out_dir or config.DEMO_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(cfg["random_seed"])

    n_sellers = int(cfg["n_sellers"])
    n_products = int(cfg["n_products"])
    n_orders = int(cfg["n_orders"])
    n_days = int(cfg["n_days"])

    start_date = pd.Timestamp("2024-01-01")

    # ---------------- sellers ----------------
    names = list(PROFILES.keys())
    probs = np.array([PROFILES[n]["weight"] for n in names], dtype="float64")
    probs = probs / probs.sum()
    seller_types = rng.choice(names, size=n_sellers, p=probs)

    sellers = pd.DataFrame(
        {
            "seller_id": [_make_id("SELLER", i) for i in range(n_sellers)],
            "seller_zip_code_prefix": rng.integers(1000, 99999, n_sellers),
            "seller_city": rng.choice(
                ["sao paulo", "rio de janeiro", "belo horizonte", "curitiba", "porto alegre"],
                n_sellers,
            ),
            "seller_state": rng.choice(STATES, n_sellers),
        }
    )
    seller_type_map = dict(zip(sellers["seller_id"], seller_types))

    # Degrading / ghost sellers get an event day part-way through the timeline.
    event_day = {}
    for sid, stype in seller_type_map.items():
        if stype == "degrading":
            event_day[sid] = int(rng.integers(int(n_days * 0.45), int(n_days * 0.70)))
        elif stype == "ghost":
            event_day[sid] = int(rng.integers(int(n_days * 0.40), int(n_days * 0.65)))

    # ---------------- products ----------------
    product_seller = rng.choice(sellers["seller_id"], size=n_products)
    products = pd.DataFrame(
        {
            "product_id": [_make_id("PROD", i) for i in range(n_products)],
            "product_category_name": rng.choice(CATEGORIES, n_products),
            "product_name_lenght": rng.integers(20, 60, n_products),
            "product_description_lenght": rng.integers(100, 2000, n_products),
            "product_photos_qty": rng.integers(1, 6, n_products),
            "product_weight_g": rng.integers(100, 15000, n_products),
            "product_length_cm": rng.integers(10, 80, n_products),
            "product_height_cm": rng.integers(5, 60, n_products),
            "product_width_cm": rng.integers(5, 60, n_products),
        }
    )
    product_to_seller = dict(zip(products["product_id"], product_seller))

    # Popularity so that order volume is skewed like a real marketplace.
    popularity = rng.pareto(1.6, n_products) + 1.0
    popularity = popularity / popularity.sum()

    # ---------------- customers ----------------
    n_customers = max(int(n_orders * 0.8), 10)
    customers = pd.DataFrame(
        {
            "customer_id": [_make_id("CUST", i) for i in range(n_customers)],
            "customer_unique_id": [_make_id("CUNIQ", i) for i in range(n_customers)],
            "customer_zip_code_prefix": rng.integers(1000, 99999, n_customers),
            "customer_city": rng.choice(
                ["sao paulo", "rio de janeiro", "salvador", "fortaleza", "recife"], n_customers
            ),
            "customer_state": rng.choice(STATES, n_customers),
        }
    )

    # ---------------- orders ----------------
    chosen_products = rng.choice(products["product_id"], size=n_orders, p=popularity)
    day_offsets = rng.integers(0, n_days, size=n_orders)

    order_rows = []
    item_rows = []
    review_rows = []
    payment_rows = []

    for i in range(n_orders):
        product_id = chosen_products[i]
        seller_id = product_to_seller[product_id]
        stype = seller_type_map[seller_id]
        prof = PROFILES[stype]

        day = int(day_offsets[i])
        purchase = start_date + pd.Timedelta(days=day, hours=int(rng.integers(6, 23)))

        cancel_p = prof["cancel"]
        late_p = prof["late"]
        handover = prof["handover"]
        delivery_mean = prof["delivery"]
        review_mean = prof["review"]

        # Apply the degradation / ghost event.
        if stype == "degrading" and day >= event_day[seller_id]:
            ramp = min((day - event_day[seller_id]) / 25.0, 1.0)
            cancel_p = 0.035 + ramp * 0.25
            late_p = 0.070 + ramp * 0.33
            handover = 1.2 + ramp * 5.5
            delivery_mean = 9.5 + ramp * 11.0
            review_mean = 4.4 - ramp * 2.2
        elif stype == "ghost":
            if day >= event_day[seller_id] + 30:
                # Listing has gone completely silent: skip the order entirely.
                continue
            if day >= event_day[seller_id]:
                cancel_p = 0.75
                handover = 9.0
                late_p = 0.65
                review_mean = 1.6

        order_id = _make_id("ORDER", i)
        customer_id = customers["customer_id"].iloc[int(rng.integers(0, n_customers))]

        roll = rng.random()
        if roll < cancel_p:
            status = "canceled" if rng.random() < 0.65 else "unavailable"
        elif roll < cancel_p + 0.03:
            status = rng.choice(["processing", "shipped", "invoiced"])
        else:
            status = "delivered"

        approved = purchase + pd.Timedelta(hours=float(abs(rng.normal(12, 6))))
        estimated = purchase + pd.Timedelta(days=float(max(rng.normal(14, 3), 5)))
        shipping_limit = approved + pd.Timedelta(days=float(max(rng.normal(3, 1), 1)))

        carrier_date = pd.NaT
        delivered_date = pd.NaT

        if status == "delivered":
            handover_days = max(float(rng.gamma(2.0, handover / 2.0)), 0.05)
            carrier_date = approved + pd.Timedelta(days=handover_days)

            base_transit = max(float(rng.normal(delivery_mean * 0.6, 2.0)), 1.0)
            delivered_date = carrier_date + pd.Timedelta(days=base_transit)

            # Force the configured share of deliveries to land after the estimate.
            if rng.random() < late_p:
                if delivered_date <= estimated:
                    delivered_date = estimated + pd.Timedelta(
                        days=float(max(rng.normal(4, 2), 0.5))
                    )
            else:
                if delivered_date > estimated:
                    delivered_date = estimated - pd.Timedelta(
                        days=float(max(rng.normal(2, 1), 0.2))
                    )
                if delivered_date <= carrier_date:
                    delivered_date = carrier_date + pd.Timedelta(days=1)

        elif status == "shipped":
            carrier_date = approved + pd.Timedelta(days=float(max(rng.normal(handover, 1), 0.2)))
        elif status in ("canceled", "unavailable"):
            approved = approved if rng.random() < 0.7 else pd.NaT

        order_rows.append(
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "order_status": status,
                "order_purchase_timestamp": purchase,
                "order_approved_at": approved,
                "order_delivered_carrier_date": carrier_date,
                "order_delivered_customer_date": delivered_date,
                "order_estimated_delivery_date": estimated,
            }
        )

        n_items = 1 if rng.random() < 0.88 else 2
        price = round(float(max(rng.gamma(2.0, 45.0), 5.0)), 2)
        freight = round(float(max(rng.normal(18, 7), 3.0)), 2)
        for item_no in range(1, n_items + 1):
            item_rows.append(
                {
                    "order_id": order_id,
                    "order_item_id": item_no,
                    "product_id": product_id,
                    "seller_id": seller_id,
                    "shipping_limit_date": shipping_limit,
                    "price": price,
                    "freight_value": freight,
                }
            )

        payment_rows.append(
            {
                "order_id": order_id,
                "payment_sequential": 1,
                "payment_type": rng.choice(
                    ["credit_card", "boleto", "voucher", "debit_card"],
                    p=[0.72, 0.19, 0.06, 0.03],
                ),
                "payment_installments": int(rng.integers(1, 10)),
                "payment_value": round(price * n_items + freight, 2),
            }
        )

        # Reviews exist for most orders.
        if rng.random() < 0.85:
            if status in ("canceled", "unavailable"):
                score = int(np.clip(round(rng.normal(1.6, 0.7)), 1, 5))
            else:
                score = int(np.clip(round(rng.normal(review_mean, 0.9)), 1, 5))
            review_rows.append(
                {
                    "review_id": _make_id("REVIEW", i),
                    "order_id": order_id,
                    "review_score": score,
                    "review_comment_title": "",
                    "review_comment_message": "",
                    "review_creation_date": purchase + pd.Timedelta(days=int(rng.integers(3, 20))),
                    "review_answer_timestamp": purchase + pd.Timedelta(days=int(rng.integers(4, 25))),
                }
            )

    orders = pd.DataFrame(order_rows)
    order_items = pd.DataFrame(item_rows)
    reviews = pd.DataFrame(review_rows)
    payments = pd.DataFrame(payment_rows)

    geolocation = pd.DataFrame(
        {
            "geolocation_zip_code_prefix": rng.integers(1000, 99999, 500),
            "geolocation_lat": rng.uniform(-33.0, -2.0, 500),
            "geolocation_lng": rng.uniform(-70.0, -35.0, 500),
            "geolocation_city": rng.choice(["sao paulo", "rio de janeiro", "recife"], 500),
            "geolocation_state": rng.choice(STATES, 500),
        }
    )

    translation = pd.DataFrame(
        {
            "product_category_name": CATEGORIES,
            "product_category_name_english": CATEGORIES,
        }
    )

    outputs = {
        "orders": orders,
        "order_items": order_items,
        "products": products,
        "sellers": sellers,
        "reviews": reviews,
        "customers": customers,
        "payments": payments,
        "geolocation": geolocation,
        "category_translation": translation,
    }

    for key, df in outputs.items():
        filename = config.OLIST_FILES.get(key)
        if filename is None:
            filename = f"{key}.csv"
        df.to_csv(out_dir / filename, index=False)

    # Ground truth for the demo only - never used by the pipeline.
    truth = pd.DataFrame(
        {"seller_id": list(seller_type_map.keys()),
         "seller_type": list(seller_type_map.values())}
    )
    truth["event_day"] = truth["seller_id"].map(event_day)
    truth.to_csv(out_dir / "DEMO_ground_truth.csv", index=False)

    (out_dir / "README_DEMO_DATA.txt").write_text(
        "THIS IS SYNTHETIC DEMO DATA.\n"
        "Generated by scripts/generate_demo_data.py for TrustCatalog.\n"
        "It follows the Olist column schema but contains no real customers,\n"
        "sellers or transactions. Delete this folder's contents and place the\n"
        "real Olist CSVs in data/raw/ to run on the real dataset.\n",
        encoding="utf-8",
    )

    if verbose:
        print("DEMO DATA GENERATED")
        print(f"  folder   : {out_dir}")
        print(f"  sellers  : {len(sellers):,}")
        print(f"  products : {len(products):,}")
        print(f"  orders   : {len(orders):,}")
        print(f"  items    : {len(order_items):,}")
        print(f"  reviews  : {len(reviews):,}")
        print("  seller mix:")
        for name, count in pd.Series(seller_types).value_counts().items():
            print(f"    {name:<10} {count}")
        print("\nNext step:  python scripts/run_pipeline.py")

    return outputs


if __name__ == "__main__":
    generate()
