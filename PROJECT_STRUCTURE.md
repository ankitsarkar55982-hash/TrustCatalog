# PROJECT_STRUCTURE.md

```
TrustCatalog/
│
├── app entry:        dashboard/app.py            (landing / login / register)
├── config:           config.py                   (all paths, weights, thresholds)
├── requirements.txt
├── run.bat                                        one-command Windows launcher
├── .env.example
├── .gitignore
├── .streamlit/config.toml                         dark theme
│
├── README.md
├── ARCHITECTURE.md
├── DEPLOYMENT.md
├── SECURITY_AUDIT.md
├── PROJECT_STRUCTURE.md                            (this file)
│
├── assets/
│   └── products/          16 offline-generated demo product images (PNG)
│
├── data/
│   ├── raw/            real Olist CSVs go here (empty by default)
│   ├── demo/            synthetic CSVs from scripts/generate_demo_data.py
│   └── processed/       exported feature CSVs from the risk pipeline
│
├── models/
│   └── isolation_forest.joblib      (created by the pipeline; not committed)
│
├── database/
│   └── trustcatalog.db              (created by the pipeline; not committed)
│
├── src/
│   │  --- risk pipeline (preserved, unmodified in behaviour) ---
│   ├── data_loader.py           find + load Olist or demo CSVs
│   ├── preprocessing.py          clean, merge, feature/outcome window split
│   ├── feature_engineering.py    seller + product behavioural features
│   ├── anomaly_detection.py      Isolation Forest train/save/load/score
│   ├── ghost_listing.py          multi-signal ghost-listing score
│   ├── risk_scoring.py           behavioural + anomaly + ghost + deterioration
│   ├── explanations.py           human-readable reasons for every flag
│   ├── evaluation.py             Precision@K / recall / F1 on temporal labels
│   ├── stress_test.py            judge stress test (in-memory injection)
│   ├── database.py               risk-pipeline SQLite read/write
│   │
│   │  --- shopping platform (new this pass) ---
│   ├── security.py               PBKDF2 hashing, input validation, upload checks
│   ├── shop_db.py                shop schema + auth/cart/orders/alerts/audit CRUD
│   ├── session.py                Streamlit login/session/RBAC/error-handling helpers
│   ├── images.py                 product image resolution + safe upload saving
│   │
│   └── __init__.py
│
├── dashboard/
│   ├── app.py                Landing: branding, login, registration, guest browse
│   ├── risk_admin.py          Original 8-page risk dashboard, reused as a module
│   └── pages/                 Streamlit native multipage routing
│       ├── 1_Shop.py            browse/search/filter/sort, product detail, cart/wishlist
│       ├── 2_Cart.py            cart review + demo checkout
│       ├── 3_My_Orders.py       customer order history/tracking
│       ├── 4_Seller_Center.py   seller dashboard/products/orders/trust status
│       └── 5_Admin_Center.py    overview/monitoring/risk/alerts/action center/audit
│
├── scripts/
│   ├── generate_demo_data.py    synthetic Olist-schema dataset (preserved)
│   ├── generate_product_images.py  NEW: generates the 16 demo product images
│   ├── run_pipeline.py          full risk pipeline (preserved)
│   ├── train_model.py           retrain just the Isolation Forest (preserved)
│   ├── selfcheck.py             pre-demo sanity check (preserved)
│   └── init_catalog.py          NEW: seed demo accounts + product catalog,
│                                 link risk-pipeline sellers to shop accounts, generate images
│
└── tests/
    ├── test_pipeline.py     8 tests  - risk pipeline (preserved, unmodified)
    ├── test_shop.py         18 tests - auth, authorization, injection, checkout
    ├── test_images.py       11 tests - image resolution, traversal, upload validation
    ├── fake_streamlit.py     minimal Streamlit/Plotly stub for offline testing
    └── dry_run_pages.py      13 headless page dry-runs, every role, pos + neg
```

## What changed vs. the original zip

**Untouched, byte-for-byte:** `config.py`, `src/data_loader.py`,
`src/preprocessing.py`, `src/feature_engineering.py`,
`src/anomaly_detection.py`, `src/ghost_listing.py`, `src/risk_scoring.py`,
`src/explanations.py`, `src/evaluation.py`, `src/stress_test.py`,
`src/database.py`, `scripts/generate_demo_data.py`,
`scripts/run_pipeline.py`, `scripts/train_model.py`,
`scripts/selfcheck.py`, `tests/test_pipeline.py`.

**Renamed, logic unchanged:** `dashboard/app.py` → `dashboard/risk_admin.py`
(`main()` replaced with `render_risk_admin()`, called from the new Admin
Center instead of being the whole app).

**New files:** `src/security.py`, `src/shop_db.py`, `src/session.py`,
`scripts/init_catalog.py`, `dashboard/app.py` (new landing page),
`dashboard/pages/*.py` (5 files), `tests/test_shop.py`,
`tests/fake_streamlit.py`, `tests/dry_run_pages.py`, `.env.example`,
`ARCHITECTURE.md`, `DEPLOYMENT.md`, `SECURITY_AUDIT.md`,
`PROJECT_STRUCTURE.md`.

**Modified:** `requirements.txt` (added `matplotlib`, comment on why no
auth library is listed), `run.bat` (added the `init_catalog.py` step),
`README.md` (rewritten for the combined platform).
