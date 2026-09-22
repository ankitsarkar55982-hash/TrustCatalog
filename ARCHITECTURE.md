# ARCHITECTURE.md

## Technology choice, and why

The mandate for this upgrade was explicit: **working application beats
impressive-looking architecture**. The original project was Streamlit +
SQLite, both already working and already tested. This upgrade keeps that
stack rather than introducing React/Next.js, FastAPI, PostgreSQL, Redis,
WebSockets, or Docker — none of which could be installed or tested in the
offline sandbox this was built in, and none of which the spec permits
introducing "merely for appearance." Every one of those has a documented
migration path below, for when the project moves to an environment with
network access.

## System diagram

```
                        ┌─────────────────────────┐
                        │   dashboard/app.py       │  landing / login / register
                        └────────────┬─────────────┘
                                     │  st.session_state["auth_user"]
                                     │  (role: CUSTOMER / SELLER / ADMIN)
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
   ┌──────────▼─────────┐ ┌──────────▼─────────┐ ┌──────────▼─────────────┐
   │ pages/1_Shop.py     │ │ pages/4_Seller_    │ │ pages/5_Admin_         │
   │ pages/2_Cart.py     │ │   Center.py        │ │   Center.py            │
   │ pages/3_My_Orders.py│ │                     │ │  → embeds              │
   │                     │ │                     │ │    dashboard/          │
   │                     │ │                     │ │    risk_admin.py       │
   └──────────┬──────────┘ └──────────┬──────────┘ └──────────┬─────────────┘
              │                       │                        │
              └───────────────────────┼────────────────────────┘
                                       │
                              ┌────────▼─────────┐
                              │  src/shop_db.py   │  parameterized SQL,
                              │  src/security.py  │  hashing, validation
                              └────────┬───────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │  database/trustcatalog.db │  ONE SQLite file
                          └────────────┬───────────────┘
                                       │  read only, by
                                       │  src/database.py
                          ┌────────────▼─────────────┐
                          │  scripts/run_pipeline.py  │  writes
                          │  src/anomaly_detection.py │  seller_features,
                          │  src/risk_scoring.py      │  risk_predictions,
                          │  src/ghost_listing.py     │  evaluation_metrics
                          └────────────────────────────┘
```

**One database file, two writers.** `scripts/run_pipeline.py` (unchanged
from before this upgrade) owns the risk-pipeline tables. `src/shop_db.py`
owns the shopping tables. Neither ever writes to the other's tables. The
bridge is one function: `shop_db.sync_risk_alerts()`, which **reads**
`risk_predictions` (written by the pipeline) and turns HIGH/CRITICAL rows
into `alerts` + `risk_history` rows — read-only with respect to the ML
side, so re-running the pipeline can never corrupt shop data and vice
versa.

## Why Streamlit's `pages/` folder, not a router

Streamlit's native multipage convention (`dashboard/pages/N_Name.py`) is
the only page-routing mechanism this Streamlit version ships with. It
means every page is a **separate top-level script** Streamlit re-executes
in full on every interaction — the reason every page starts with
`st.set_page_config()`, a login/role check, and its own `import` block.
This is why authorization is enforced with a guard function
(`session.require_role(...)`) at the top of each page rather than a
central router: there is no central router in this framework.

## Preserving the original risk dashboard

`dashboard/risk_admin.py` is the original 897-line dashboard's page
functions (`page_overview`, `page_seller_risk`, `page_ghost_listings`,
`page_stress_test`, etc.), unchanged, with `main()` replaced by
`render_risk_admin()` — a function `pages/5_Admin_Center.py` calls after
its own admin-only auth check. Every chart, filter, and the Judge Stress
Test button behave exactly as before; they are simply reached through the
Admin Center's "Risk Intelligence" tab instead of being the whole app.

## "Near real-time," precisely

Section 3 of the upgrade spec is explicit: don't claim true real-time if
it isn't. `pages/5_Admin_Center.py`'s Monitoring tab is Streamlit
re-querying SQLite on each rerun (page load, or a "Refresh now" button) —
polling, not a push mechanism. The UI says exactly this: **"Near
Real-Time Monitoring... it is Streamlit polling the database, not a
WebSocket push."** A genuine WebSocket/SSE layer would need a persistent
backend process outside Streamlit's request-response model — see
"Real-time upgrade path" below.

## Database schema (shop tables)

```
users ──┬── seller_profiles (1:1, sellers only, holds risk_seller_id link)
        ├── catalog_products (1:many, as seller_user_id)
        ├── cart_items (1:many)
        ├── wishlist_items (1:many)
        ├── orders (1:many, as customer_user_id)
        └── audit_logs / admin_actions (referenced by user_id)

catalog_products ──┬── cart_items
                    ├── wishlist_items
                    └── order_items

orders ── order_items (1:many)

risk_history, alerts: keyed by (entity_type, entity_id) — entity_id is a
risk-pipeline seller_id (e.g. "SELLER000042"), bridging to seller_profiles
via seller_profiles.risk_seller_id.
```

Full DDL: `src/shop_db.py::SCHEMA`.

## Testing architecture

Three layers, because this sandbox cannot install Streamlit itself
(no network access):

1. **`tests/test_pipeline.py`** (8 tests) — the original ML pipeline,
   unchanged.
2. **`tests/test_shop.py`** (18 tests) — pure Python calls into
   `src/shop_db.py` against a throwaway SQLite file. Covers auth,
   authorization/IDOR, SQL-injection resilience, checkout transactions,
   and the risk-alert bridge.
3. **`tests/dry_run_pages.py`** (13 dry-runs) — a minimal fake Streamlit
   runtime (`tests/fake_streamlit.py`) that lets every page's *actual*
   code execute headlessly under each role, including real form
   submissions and button clicks, with results checked against the real
   database afterward. This catches `NameError`/`AttributeError`/crash
   bugs in the UI logic that pure unit tests can't reach, without needing
   `pip install streamlit`. It found and fixed two real bugs during
   development (see `README.md`, "Bugs found and fixed").
   **What it cannot catch:** CSS/layout problems, or anything about how
   the page actually looks — only that the Python executes correctly.

## Migration paths (documented, not implemented — see SECURITY_AUDIT.md §15 for why)

### Production database: SQLite → PostgreSQL
`src/shop_db.py` uses only standard SQL (parameterized queries, no
SQLite-specific functions except `date('now')` in
`marketplace_counts()`). Swapping `sqlite3.connect()` for `psycopg2` or
`asyncpg`, changing `?` placeholders to `%s`, and replacing `date('now')`
with `CURRENT_DATE` would port the schema with minimal changes. The
`src/database.py` risk-pipeline reader (`pandas.read_sql_query`) is
already DB-agnostic through SQLAlchemy-style connection strings if
`pandas`/`sqlalchemy` are pointed at a Postgres URL instead.

### Real-time: polling → WebSockets/SSE
Streamlit's request-response model doesn't support server-push. A real
implementation would run a small separate process (FastAPI + WebSockets,
or Postgres `LISTEN/NOTIFY` fed to an SSE endpoint) that the Streamlit
app's frontend JS component subscribes to — a substantial, separately
testable piece of work, intentionally out of scope for this pass per the
"don't add unverified tech" instruction.

### Background jobs: none currently → Celery/RQ
There are no background jobs yet — `sync_risk_alerts()` runs synchronously
when an admin clicks the button, and the ML pipeline runs synchronously
via `scripts/run_pipeline.py`. If risk syncing needed to run on a
schedule, a lightweight scheduler (APScheduler in-process, or `RQ` with a
Redis broker) would be the next addition — not added here because
neither was needed for anything this pass actually built, per "don't
introduce unnecessary technologies."
