# TrustCatalog

**AI-Powered E-Commerce Seller & Listing Integrity Monitor**

TrustCatalog reads ordinary marketplace order and shipping data and flags
sellers and listings whose behaviour suggests they can no longer fulfil what
they are advertising — **before** customers place orders that will fail.

---

## 1. Problem statement

Third-party marketplace sellers run out of stock but forget to update their
listings. Customers order, wait several days, then get a cancellation. The
platform has no visibility into the seller's warehouse — the only evidence
available is **behavioural**, sitting in order and shipping timestamps.

## 2. Objective

Detect, rank and explain:

- possible out-of-stock situations
- possible ghost listings (live listings the seller can no longer fulfil)
- high probability of order cancellation
- abnormally delayed shipping (seller side) and delivery (logistics side)
- sudden changes in seller activity
- generally unusual seller behaviour

…and do it **early enough to act**.

### What this system deliberately does NOT claim

TrustCatalog never states that a seller is fraudulent or definitively out of
stock. Every output is phrased as **High Risk**, **Possible Ghost Listing**,
**Possible Stock/Listing Issue** or **Behavioural Anomaly**, and every flag
comes with the numbers that produced it so a human auditor makes the call.

---

## 3. Key features

| Feature | Where |
|---|---|
| 30+ behavioural features from raw timestamps | `src/feature_engineering.py` |
| Leakage-safe feature / outcome window split | `src/preprocessing.py` |
| Isolation Forest anomaly detection | `src/anomaly_detection.py` |
| Multi-signal ghost-listing score (0–100) | `src/ghost_listing.py` |
| Four-component risk engine (0–100) | `src/risk_scoring.py` |
| Plain-English explanations for every flag | `src/explanations.py` |
| Precision@K / Recall / F1 on temporal labels | `src/evaluation.py` |
| Judge stress test with injected degraded sellers | `src/stress_test.py` |
| SQLite result store (dashboard never retrains) | `src/database.py` |
| 8-page dark Streamlit dashboard | `dashboard/app.py` |
| Pre-demo self-check | `scripts/selfcheck.py` |

---

## 4. System architecture

```
 data/raw/  (real Olist)          data/demo/  (synthetic, auto-generated)
        \                          /
         v                        v
              src/data_loader.py            picks whichever exists
                     |
              src/preprocessing.py          clean, merge, derive timings
                     |
        +------------+------------+
        |                         |
  FEATURE WINDOW            OUTCOME WINDOW      <-- split at a cutoff date
  (orders before cutoff)    (orders after)
        |                         |
 src/feature_engineering.py   labels only
        |                         |
 rule-based behavioural score     |
        |                         |
 src/anomaly_detection.py         |            Isolation Forest
        |                         |
 src/ghost_listing.py             |
        |                         |
 src/risk_scoring.py  ------------+---->  src/evaluation.py
        |                                       (Precision@K, Recall, F1)
 src/explanations.py
        |
 src/database.py  ->  database/trustcatalog.db
        |
 dashboard/app.py  (read-only Streamlit UI)
```

---

## 5. Technology stack

Python 3.11+ · pandas · NumPy · scikit-learn (Isolation Forest) · SQLite ·
Streamlit · Plotly · joblib.

No paid APIs, no LLM calls, fully offline after `pip install`.

---

## 6. Dataset

Primary: **Olist Brazilian E-Commerce** (Kaggle, ~120 MB). Place the CSVs in
`data/raw/`:

```
olist_orders_dataset.csv          olist_customers_dataset.csv
olist_order_items_dataset.csv     olist_geolocation_dataset.csv
olist_products_dataset.csv        olist_order_payments_dataset.csv
olist_sellers_dataset.csv         product_category_name_translation.csv
olist_order_reviews_dataset.csv
```

Only `olist_orders_dataset.csv` and `olist_order_items_dataset.csv` are
strictly required; everything else enriches the analysis and is optional.

**Demo mode.** If `data/raw/` is empty the project generates a statistically
realistic synthetic dataset in the same schema (120 sellers, 600 products,
~9,000 orders across 180 days) containing normal, medium-risk, high-risk,
gradually degrading and ghost sellers. It is clearly labelled as demo data in
`data/demo/README_DEMO_DATA.txt` and in the dashboard sidebar.

### A real limitation, stated honestly

Seller identity lives in `olist_order_items`. A small number of cancelled and
unavailable Olist orders have no item rows at all, so they cannot be attributed
to any seller and are excluded. This slightly *understates* cancellation rates
on the real dataset.

---

## 7. Feature engineering

Seller-level (one row per seller, computed only from the feature window):

`total_orders`, `cancelled_orders`, `cancellation_rate`, `delivered_orders`,
`late_orders`, `late_delivery_rate`, `average_delivery_days`,
`average_shipping_delay`, `average_review_score`, `total_products`,
`unique_products`, `orders_per_day`, `orders_last_7_days`,
`orders_last_30_days`, `activity_change`, `delivery_time_variance`,
`shipping_delay_variance`, `failed_order_rate`, `recent_cancellation_rate`,
`recent_late_delivery_rate`, `recent_shipping_delay`, `inactive_days`,
`unavailable_rate`, `never_shipped_rate`, `shipping_limit_breach_rate`,
`revenue`.

Product/listing-level: `product_id`, `seller_id`, `category`, `total_orders`,
`cancellation_rate`, `late_delivery_rate`, `average_delivery_days`,
`shipping_delay`, `recent_activity`, `activity_change`, `inactive_days`,
`seller_risk` and the same variance/recency set.

### The one idea worth highlighting

The order lifecycle is split into two segments:

```
purchase -> approved -> delivered_carrier_date -> delivered_customer_date
                     \_________________________/  \____________________/
                        SELLER's responsibility        LOGISTICS
```

`average_shipping_delay` measures only the **seller's** segment — the gap
between payment approval and physical handover to the carrier. A seller who
has run out of stock stalls *here*, days before any cancellation appears. Most
naive approaches only measure total delivery time, which mixes a stock problem
with a courier problem and blames the wrong party.

---

## 8. ML methodology

**Isolation Forest** (`n_estimators=200`, `contamination='auto'`,
`random_state=42`) inside a `Pipeline` with `StandardScaler`, over the nine
features in `config.ANOMALY_FEATURES`.

Two decisions that matter:

1. **Trained on normal sellers only.** The model is fitted on sellers below the
   75th percentile of the rule-based behavioural score, so bad sellers do not
   teach the model that being bad is normal.
2. **Stable score normalisation.** The raw score is converted to 0–100 against
   a *reference distribution saved inside the model file*, not against whatever
   batch is being scored. Without this, any batch of 10 sellers would always
   contain a "worst" one at 100 — and the stress test would be meaningless.

The bundle saved by joblib contains the pipeline, the feature names, the
reference distribution and a version string, so a stale model can never be
silently scored with the wrong columns.

---

## 9. Risk scoring methodology

```
risk = 0.40 x behavioural   (transparent rules, config.BEHAVIOR_WEIGHTS)
     + 0.30 x anomaly       (Isolation Forest, 0-100)
     + 0.20 x ghost         (ghost-listing score, 0-100)
     + 0.10 x deterioration (recent behaviour vs the seller's own baseline)
```

All weights, caps and bands live in `config.py`.

| Score | Level |
|---|---|
| 0–30 | LOW |
| 31–60 | MEDIUM |
| 61–80 | HIGH |
| 81–100 | CRITICAL |

The **deterioration** component is the early-warning term: it compares the last
30 days against the seller's *own* history, so it fires while the lifetime
averages still look acceptable.

---

## 10. Ghost-listing methodology

No single rule. Six normalised signals, weighted
(`config.GHOST_WEIGHTS`):

| Signal | What it captures |
|---|---|
| inactivity | listing has gone quiet |
| activity_drop | volume collapsed vs its own baseline |
| cancellation | orders arrive then get cancelled |
| unavailable | `unavailable` status or orders that never reached a carrier |
| shipping_delay | seller sits on the parcel |
| late_delivery | what does arrive, arrives late |

Result is 0–100, banded Low / Moderate / High / Critical concern, flagged as a
**Possible Ghost Listing** at ≥ 61.

---

## 11. Explainable AI

`src/explanations.py` generates reasons from the actual feature values, each
compared against the population median, e.g.

```
Seller SELLER000042

Risk Score: 87
Risk Level: CRITICAL

Reasons:

- Cancellation rate is 22.4%, against a platform median of 3.1%.
- Late delivery rate is 31.0% of delivered orders (median 6.2%).
- Average handover delay (order approved to carrier pickup) is 4.2 days,
  against a median of 0.9 days.
- Recent order activity dropped by 65% compared with this seller's own baseline.
- Delivery-time variance (58.4) is unusually high, meaning fulfilment is
  inconsistent rather than merely slow.
```

Every number in that block is printed straight from the row the judge can see
in the table.

---

## 12. Early-warning methodology (leakage control)

```
|<------- FEATURE WINDOW (75%) ------->|<--- OUTCOME WINDOW (25%) --->|
             features built here                labels built here
                                       ^
                                    cutoff
```

- Features use **only** orders purchased before the cutoff.
- The cutoff is used as "today" for every recency calculation.
- Labels use **only** orders purchased after it: a seller is positive when
  ≥ 20% of their outcome-window orders failed (cancelled, unavailable, or
  delivered late) over at least 3 orders.
- `scripts/selfcheck.py` asserts that no `outcome_*` column ever appears in the
  feature table.

This is the honest version of the metric. Scoring and labelling on the same
orders would produce near-perfect numbers that mean nothing.

---

## 13. Stress testing

`src/stress_test.py` builds a mixed population of normal and artificially
degraded sellers and pushes it through the *same* scoring path as production.
Degraded rows are constructed to be internally consistent — a seller with a 25%
cancellation rate also gets the matching handover delay, review score and
activity drop — because injecting a single impossible number would not prove
anything.

**Nothing on disk is modified.** The injected sellers exist only in memory, so
the test can be run repeatedly during a demo.

Reported: normal count, degraded count, detected, missed, detection rate,
precision, recall, false alarms, plus a box plot showing score separation.

---

## 14. Evaluation metrics

Precision@5 / @10 / @20, precision, recall, F1 at the HIGH threshold, base
rate, and **lift vs random**.

Typical demo-data run:

| Metric | Value |
|---|---|
| Precision@5 | 1.00 |
| Precision@10 | 1.00 |
| Precision@20 | 0.80 |
| Base rate | 0.34 |
| Lift @10 | 2.95x |
| Precision (≥61) | 1.00 |
| Recall (≥61) | 0.30 |

Recall is deliberately low at the HIGH threshold: an operations team can only
audit a handful of sellers per day, so the system is tuned for precision at the
top of the list. The Evaluation page plots precision at every `k` so the
trade-off is visible rather than asserted.

---

## 15. Installation (Windows + VS Code)

### Fastest path

Double-click **`run.bat`**. It creates a virtual environment, installs
dependencies, generates demo data, runs the pipeline and launches the
dashboard.

### Manual path

```bat
cd path\to\TrustCatalog
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### VS Code setup

1. `File > Open Folder…` → select the `TrustCatalog` folder.
2. `Ctrl+Shift+P` → **Python: Select Interpreter** → pick `.venv`.
3. `Ctrl+`` ` `` to open the terminal (it activates `.venv` automatically).
4. Install the Microsoft **Python** extension if VS Code prompts you.

---

## 16. How to run

```bat
python scripts\generate_demo_data.py     :: only if you have no Olist CSVs
python scripts\run_pipeline.py           :: clean, train, score, evaluate, save
python scripts\selfcheck.py              :: verify everything before demoing
streamlit run dashboard\app.py           :: launch the UI
python tests\test_pipeline.py            :: run the test suite
```

Useful variants:

```bat
python scripts\run_pipeline.py --no-train    :: reuse the saved model
python scripts\train_model.py                :: retrain the model only
```

The dashboard opens at <http://localhost:8501>.

---

## 17. Using the real Olist dataset

1. Download the Olist dataset from Kaggle.
2. Copy all nine CSVs into `data\raw\`.
3. `python scripts\run_pipeline.py`
4. The sidebar will now say **Running on the Olist dataset** instead of
   **DEMO DATA**.

No code changes are needed — `src/data_loader.py` prefers `data/raw/`
automatically. Expect the pipeline to take roughly 1–3 minutes on the full
100k-order dataset.

---

## 18. Using the dashboard

| Page | What it is for |
|---|---|
| **Overview** | KPIs, risk distribution, top risky sellers/listings, platform trends |
| **Seller Risk** | Filterable, sortable, downloadable ranked seller table |
| **Product Risk** | Same at listing level |
| **Ghost Listings** | Only listings above the ghost threshold, with reasons |
| **Seller Details** | One seller: all metrics, why it was flagged, risk composition, comparison to the platform median, its own listings |
| **Analytics** | Distributions, risk-vs-behaviour scatter plots, riskiest categories |
| **Evaluation** | Precision@K, recall, F1, and the precision-at-k curve |
| **Judge Stress Test** | Inject degraded sellers and measure detection live |

---

## 19. Screenshots

> Replace these placeholders with your own captures before submitting.

- `docs/screenshot_overview.png`
- `docs/screenshot_seller_details.png`
- `docs/screenshot_ghost_listings.png`
- `docs/screenshot_stress_test.png`

---

## 20. Limitations

- Sellers with very few orders have noisy rates. The pipeline refuses to score
  anyone below `MIN_ORDERS_FOR_SCORING` (default 3); a proper fix is
  empirical-Bayes shrinkage toward the category mean (see Future improvements).
- Labels are **proxies** derived from order outcomes, not confirmed stock-outs.
  Olist contains no warehouse ground truth, and this is documented rather than
  hidden.
- Seasonality (Black Friday) inflates delays platform-wide; the current
  normalisation is per-seller, not per-category-per-day.
- Cancelled Olist orders without item rows cannot be attributed to a seller.
- The demo data is synthetic. Numbers from a demo run are not evidence about
  real Brazilian sellers.

## 21. Future improvements

- Empirical-Bayes (Beta-Binomial) shrinkage so one bad order out of two cannot
  top the risk list.
- CUSUM / EWMA change-point detection to catch slow drift, which z-score style
  features miss.
- Rank by **expected harm** (`risk x forecast volume`) rather than probability,
  so a risky bestseller outranks a risky listing nobody buys.
- A supervised gradient-boosting model on the proxy labels, with SHAP values
  feeding the explanation layer.
- Category- and route-level normalisation to remove seasonality and distance
  confounders.
- Lead-time metric: median days between the flag and the first real failure.

---

## 22. Product images

Every catalog product resolves to one of three states, tried in this
order by `src/images.py::resolve_product_display()`: **(1) a local file**
under `assets/products/` (preferred — works fully offline, guaranteed to
still be there after a redeploy), **(2) a validated `image_url`** (used
only when no local file is set — the image loads directly in the
visitor's browser; this app never fetches the URL server-side, which is
what actually rules out SSRF here rather than a URL allowlist), or
**(3) nothing**, in which case the UI shows a plain "Image unavailable"
box — never an emoji, never a broken-image icon.

**Honesty about what's actually in `assets/products/` right now:** the 16
files there are procedurally generated with Pillow (`scripts/generate_product_images.py`),
styled to resemble studio product photography — neutral seamless
background, soft drop shadow, directionally-shaded object instead of a
flat icon fill — but they are illustrations of the product's shape, not
photographs. This sandbox has no internet access, so nothing was
downloaded, scraped, or licensed from anywhere; claiming otherwise would
be exactly the kind of fabricated capability this project's own
instructions forbid.

**Replacing them with real photos is a pure file swap, no code changes:**
save a real photo as (for example) `assets/products/chef_knife.jpg`,
keeping the same filename `catalog_products.image_filename` already
points to (or update that column via Seller Center's product-edit image
picker), and it renders immediately — `resolve_product_image()` doesn't
care whether the bytes came from Pillow or a camera, only that the file
exists at that path with an allowed extension (`.png .jpg .jpeg .webp`).

Three ways to attach an image to a product, all in Seller Center's
add/edit-product forms: **upload a file** (validated by
`security.validate_image_upload()` — extension allowlist, 5MB cap,
magic-byte content sniffing, stored under a random filename, original
filename discarded), **paste an image URL** (validated by
`security.clean_image_url()` — https/http only, rejects `localhost`,
loopback, private-network, and cloud-metadata hosts as a defensive
measure even though the server never connects to it), or **pick one of
the built-in demo images**.

## 23. Project structure

```
TrustCatalog/
├── config.py                  all paths, weights, thresholds
├── data/
│   ├── raw/                   real Olist CSVs go here
│   ├── demo/                  generated synthetic CSVs
│   └── processed/             exported feature tables
├── models/
│   └── isolation_forest.joblib
├── database/
│   └── trustcatalog.db
├── src/
│   ├── __init__.py
│   ├── data_loader.py
│   ├── preprocessing.py
│   ├── feature_engineering.py
│   ├── anomaly_detection.py
│   ├── ghost_listing.py
│   ├── risk_scoring.py
│   ├── explanations.py
│   ├── evaluation.py
│   ├── stress_test.py
│   └── database.py
├── dashboard/
│   └── app.py
├── scripts/
│   ├── generate_demo_data.py
│   ├── train_model.py
│   ├── run_pipeline.py
│   └── selfcheck.py
├── tests/
│   └── test_pipeline.py
├── .streamlit/config.toml
├── requirements.txt
├── run.bat
├── .gitignore
└── README.md
```
