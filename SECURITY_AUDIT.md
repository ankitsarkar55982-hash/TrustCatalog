# SECURITY_AUDIT.md

**Status: Security-Hardened Prototype.** Not "100% secure," not
"unhackable," not "production ready" as a standalone claim — see
§15 for exactly what stands between this and a real deployment.

This document describes what was implemented, what was tested (with
commands to reproduce every result), and what remains a genuine gap.

---

## 1. Authentication

- **Hashing:** PBKDF2-HMAC-SHA256, 260,000 iterations, 16-byte random salt
  per user (`src/security.py::hash_password`). This is a standard,
  NIST-approved (SP 800-132) KDF — not a homemade algorithm.
- **Why not bcrypt/argon2id:** this development sandbox has no network
  access, so `pip install argon2-cffi` fails offline. PBKDF2 via Python's
  stdlib `hashlib` needed no install.
- **PBKDF2 is not equivalent to Argon2id.** Argon2id is memory-hard by
  design, which makes large-scale GPU/ASIC password cracking far more
  expensive than it is against PBKDF2; PBKDF2-HMAC-SHA256 is CPU-bound
  and comparatively cheaper to attack at scale even at a high iteration
  count. **Argon2id is recommended before any real deployment** — see
  §15. The swap is isolated to two functions in one file; the stored-hash
  format already embeds the algorithm name and iteration count so old
  hashes keep verifying after the swap.
- Constant-time comparison (`hmac.compare_digest`) prevents timing attacks
  on the hash comparison itself.
- Login returns an identical error for "no such user" and "wrong
  password," so login cannot be used to enumerate registered usernames.
- Disabled accounts (`users.is_active = 0`) cannot authenticate.
- **Tested:** `tests/test_shop.py::test_password_hash_roundtrip`,
  `test_password_not_stored_plaintext`, `test_login_rejects_wrong_password_and_unknown_user`,
  `test_disabled_account_cannot_login`. All pass — run
  `python tests/test_shop.py`.

## 2. Authorization (RBAC)

- Three roles: `CUSTOMER`, `SELLER`, `ADMIN`, enforced by a SQL `CHECK`
  constraint on `users.role` (a fourth value cannot even be inserted).
- The role is set **once**, at registration, from server-side code that
  reads it back from the row `authenticate()` returned — never from a URL,
  query string, form field, or client-side value. There is no code path
  anywhere that lets a logged-in user change their own `role`.
- Every protected Streamlit page calls `session.require_role(...)` as its
  first action; on failure it renders a plain "Access denied" message and
  calls `st.stop()` — the rest of the page's code never executes.
- **Known limitation:** Streamlit's multipage sidebar lists every page's
  link to every viewer regardless of role — Streamlit does not support
  hiding a page link server-side in the version pinned here. This is a
  UX gap, not an authorization gap: clicking a link a role shouldn't see
  still hits the `require_role()` guard and is denied. Documented rather
  than hidden, per the honesty requirement.
- Data-level authorization is enforced in the SQL itself, not just in the
  page: e.g. `update_product(... seller_user_id)`'s `UPDATE` statement has
  `WHERE id = ? AND seller_user_id = ?` — a seller literally cannot write
  a row that isn't theirs, no matter what the UI does or doesn't show.
- **Tested (all pass):**
  - `test_seller_cannot_edit_another_sellers_product` — Seller B cannot
    edit Seller A's product; row is verified unchanged afterward.
  - `test_customer_cannot_read_another_customers_order` — Customer 2
    gets `None` reading Customer 1's order.
  - `test_seller_cannot_update_status_on_order_without_their_item` —
    raises `CheckoutError` rather than allowing the update.
  - `tests/dry_run_pages.py` — headless negative dry-runs: a CUSTOMER
    account opening `4_Seller_Center.py` and `5_Admin_Center.py`, and a
    SELLER account opening `5_Admin_Center.py`, all correctly hit
    `st.stop()` before any protected content renders. 3/3 pass.

## 3. Database security

- Every query in `src/shop_db.py` uses `?` parameterized placeholders.
  There is no string-formatted or f-string-interpolated SQL anywhere in
  the file — this is the actual SQL-injection defense.
- Foreign keys are enabled (`PRAGMA foreign_keys = ON`) and declared on
  every child table (`cart_items`, `order_items`, `alerts`, etc.), so an
  orphaned row referencing a deleted user/product is structurally
  impossible.
- Checkout runs inside an explicit transaction (`BEGIN IMMEDIATE` /
  `commit` / `rollback`): either every row (order, order_items, stock
  decrement, cart clear) is written, or none are.
- **Tested:** `test_sql_injection_like_input_is_inert` — a search query
  containing `'; DROP TABLE users; --` is treated as inert text (zero
  results, `users` table intact). `test_checkout_prevents_overselling` —
  two customers racing for the last 2 units of a product; the second
  checkout is rejected and leaves **zero** partial rows in `orders`/
  `order_items`, verified directly against the database.

## 4. Input validation

`src/security.py` validates every user-controlled field before it reaches
a database write: email format, username charset/length, password
strength (min 8 chars, common-password blocklist), price (non-negative,
capped), quantity (non-negative, capped), free text (length-capped,
control characters stripped).

**Tested:** `test_input_validation_rejects_bad_values` (bad email, short
username, weak password, invalid role) and
`test_negative_price_and_quantity_rejected` — both pass.

## 5. File upload security

Product image upload is implemented with:
- extension allowlist (`.png .jpg .jpeg .webp`),
- 5 MB size cap,
- **magic-byte content sniffing** — a file renamed to `.png` that isn't
  actually PNG/JPEG/WebP data is rejected, catching the classic
  renamed-executable trick,
- the original filename is **discarded**; a random hex filename is
  generated instead, so a path-traversal filename (`../../etc/passwd.png`)
  can never reach the filesystem as a path — it only ever reaches the
  filesystem as one opaque token.

**Tested:** `test_upload_validation` — rejects `.exe`, rejects a `.png`
whose bytes aren't a real PNG, and confirms a traversal-shaped filename
still returns a safe opaque name.

**Update (image-pipeline pass):** the upload widget is now wired in —
Seller Center's add/edit product forms expose a real `st.file_uploader`,
which calls `src.images.save_uploaded_product_image()` →
`security.validate_image_upload()` before anything touches disk. A
second, independent layer of defense sits at the read side:
`src.images.resolve_product_image()` takes only the basename of whatever
filename is stored, checks it against the same extension allowlist, and
confirms the file exists under `assets/products/` before returning it —
so even a bad value that somehow reached the database (it can't, per
`security.clean_image_filename()` at write time) could never resolve to
a path outside that directory. Tested in `tests/test_images.py`: valid
resolution, missing-file fallback, disallowed extension, four different
path-traversal-shaped filenames, and that an upload rejected by
validation writes nothing to disk.

**Update (image-URL pass):** sellers/admins may now also attach a product
image by pasting a URL instead of uploading a file or picking a demo
image. `security.clean_image_url()` validates the URL's *shape* only
(scheme must be http/https, must have a host, host must not be
`localhost`/loopback/a private-network address/the cloud-metadata
endpoint `169.254.169.254`) — it never makes a server-side HTTP request
to the URL. That last point is the actual SSRF defense: there is no
outbound fetch for an attacker to redirect at an internal service,
because the browser loads the image directly, exactly like an ordinary
`<img>` tag on any web page. The host blocklist is a second, defensive
layer on top of that, not the primary control. Tested in
`tests/test_images.py`: valid https accepted, four disallowed schemes
rejected (`javascript:`, `file:`, `ftp:`, `data:`), five SSRF-shaped
hosts rejected (localhost, 127.0.0.1, the AWS/GCP/Azure metadata IP,
and two private-network ranges), and that `update_product()` enforces
the same validation as `create_product()`.

## 6. Secret management

- No password, API key, or token is hard-coded anywhere in the source —
  verified with `grep -rniE "password|secret|api_key|token" --include="*.py"`
  returning zero hits outside this documentation and the demo-credential
  constant (which is clearly labeled DEMO ONLY, section 11).
- `.env.example` ships with placeholder values only; `.env` itself is
  listed in `.gitignore` and was never created in this project.

## 7. Session security

- `st.session_state["auth_user"]` is written in exactly one place
  (`src/session.py::login`), and only from the row `authenticate()`
  returns after a verified password check.
- `logout()` clears it. Every protected page re-checks
  `session_state` on every rerun (Streamlit's execution model re-runs
  the whole script on every interaction), so there is no "protected page
  cached from before logout" state.
- Nothing beyond the user row (id, username, role, display_name) is kept
  in session state — no password, no hash, no token.

## 8. Privacy

- Sellers can only query their own orders (`list_orders_for_seller`
  filters `seller_user_id`) — they never see another seller's customer
  list or another seller's order totals.
- The audit log stores `username` and a `detail` string, but the code
  writing `detail` never includes a password or token — confirmed by
  `test_audit_log_never_contains_password`, which attempts a failed
  login with a real password and a wrong guess and asserts neither
  string appears anywhere in the log.

## 9. Audit logging

Every security-relevant event is recorded in `audit_logs`: registration,
login success/failure (with reason, never with the password), logout,
product create/update, order creation, order status change, alert
resolution, account enable/disable. Admin-facing consequential actions
(disable account, resolve alert, deactivate product) additionally write
to `admin_actions` with the acting admin's id and an optional note.

**Tested:** `test_alert_resolution_writes_audit_and_admin_action`.

## 10. Error handling

`src/session.py::friendly_errors()` wraps each page's body: any
*unexpected* exception is logged with its full traceback to the server
console (visible to whoever ran `streamlit run`, never to the browser)
and the viewer sees "Something went wrong. Please try again." Expected,
specific errors (`ValidationError`, `AuthError`, `CheckoutError`) are
deliberately excluded from this catch-all so pages can show their
specific, actionable message instead of a generic one.

Verified indirectly: no test or dry-run in this project ever surfaced a
raw Python traceback in place of a friendly message (see §13's dry-run
output).

## 11. Demo credentials

Created by `python scripts/init_catalog.py` — **DEMO ONLY, change before
any real deployment**:

| Role | Username | Password |
|---|---|---|
| Customer | `demo_customer` | `DemoPass!2026` |
| Seller | `demo_seller` | `DemoPass!2026` |
| Admin | `demo_admin` | `DemoPass!2026` |

## 12. Dependency review

`requirements.txt` contains exactly what is imported: pandas, numpy,
scikit-learn, streamlit, plotly, joblib, matplotlib (for table color
gradients). No auth library is listed because none is used — password
hashing uses only Python's standard library (§1). No unused dependency
was found in the audit of the original ML pipeline.

## 13. Security tests — how to reproduce every claim above

```bat
python tests/test_pipeline.py      :: 8 tests  - ML pipeline (unchanged, still passing)
python tests/test_shop.py          :: 18 tests - auth, authorization, injection, checkout
python tests/test_images.py        :: 19 tests - image resolution, traversal, upload + URL/SSRF validation
python tests/dry_run_pages.py      :: 13 dry-runs - every page, every role, positive + negative
python scripts/selfcheck.py        :: dashboard/database wiring sanity check
```

**Result at time of writing: 58/58 automated checks pass, plus selfcheck
green.** `tests/dry_run_pages.py` is a project-specific harness (see its
docstring) that executes each page's real logic under a fake Streamlit
runtime, because this offline sandbox cannot `pip install streamlit` to
run the real one — it is not a substitute for actually clicking through
the live app once dependencies are installed, and §15 says so explicitly.

## 14. Passed / failed

**Passed (58/58):** everything listed in §13.

**Not tested (no automated coverage yet):** rate limiting / brute-force
login throttling (not implemented — see §15), CSRF (Streamlit's own
session model handles this differently from a typical form-based web
app and wasn't separately audited), concurrent-write stress beyond the
two-customer race test in §3.

## 15. Known limitations / production requirements

This prototype is **not** a production deployment. Before handling real
users or real money, it would need:

- **Argon2id or bcrypt** in place of PBKDF2 (§1) — trivial swap, not yet
  done because this sandbox has no network access.
- **HTTPS** — Streamlit Community Cloud and most PaaS hosts (Render,
  Railway) terminate TLS for you automatically; nothing in this app
  needs to change, but it must be deployed behind one of them, not run
  bare.
- **Rate limiting / login throttling** — not implemented. A determined
  attacker could currently attempt unlimited password guesses against
  `authenticate()`. Streamlit doesn't have built-in support for this; a
  reverse-proxy-level rate limit (e.g. on the hosting platform) or an
  application-level counter keyed by IP would be the fix.
- **A production database** — SQLite is fine for a single-process demo;
  see `ARCHITECTURE.md` for the PostgreSQL migration path, which was
  deliberately *not* implemented here because it can't be tested without
  a running Postgres instance (this sandbox has neither network nor a
  Postgres server).
- **Backups, monitoring, incident response, dependency-update process**
  — organizational practices, not code, and out of scope for a prototype.
- **A real payment processor**, if real transactions are ever wanted —
  today the app uses simulated `DEMO_PAID`/`DEMO_FAILED` status and never
  collects real payment details (see "Checkout system" in README.md).
- **Real image storage** — file-upload validation is implemented and
  tested (§5), but no page currently exposes the upload widget; product
  images are placeholder icons.

None of the above were skipped by oversight — each is called out here
specifically so nobody mistakes "prototype" for "production."
