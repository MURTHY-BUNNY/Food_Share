# FoodShare — project handoff

A complete description of what this project is, how it is built, and what state
it is in. Written to be handed to another person or an AI assistant as-is.

Last updated: 2026-10-28.

> **Secrets are deliberately excluded.** Database password, JWT secrets and the
> admin password hash live only in `backend/.env` and are shown here as
> placeholders. Do not paste that file into a chat or a document.

---

## 1. What the project is

FoodShare is a food-donation and food-access web application. Donors publish
surplus food with a pickup deadline; anyone can browse what is available,
open a listing to see the contact number, and call to collect it. Donors manage
their own listings from a dashboard, and an administrator polices the data from
a console: changing statuses, deleting test rows, verifying organization
accounts and triaging food requests.

Three kinds of user:

| Role | How they get in | What they can do |
| --- | --- | --- |
| Visitor | no account | browse available food, open a listing, submit a food request, track a donation by id |
| Donor (Individual or Organization) | email + password, email verified by a 6-digit code | publish, edit, complete and cancel their own donations, see their own statistics |
| Administrator | single account defined in `.env` | everything above plus status changes, deletion, organization verification, food requests, email-delivery diagnostics |

Architecture is deliberately simple: a **FastAPI + MySQL** JSON API, and a
**static HTML/CSS/JavaScript frontend** with no build step and no framework.
Each page is a plain `.html` file that loads a shared `js/api.js` module plus a
page-specific script.

---

## 2. How to run it

Two processes are needed: the API and a static file server.

```powershell
# 1) API — from the backend folder, using the project virtualenv
cd backend
.\venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
# docs at http://127.0.0.1:8000/docs

# 2) Frontend — from the repository root, on any port
python -m http.server 5500 --bind 127.0.0.1
# open http://127.0.0.1:5500/html/index.html  (VS Code Live Server works too)
```

- API base URL is `http://127.0.0.1:8000`, overridable per page by setting
  `window.FOODSHARE_API_BASE` before `js/api.js` loads.
- MySQL must be running, and the database named in `DB_NAME` must exist.
  Tables are created and upgraded automatically at startup
  (`initialize_database()` on the FastAPI lifespan hook).
- CORS allows `http://localhost:<any port>`, `http://127.0.0.1:<any port>` and
  the `null` origin (opening a file directly from disk).

---

## 3. Configuration reference (`backend/.env`)

Read once at startup, so **restart the API after changing it**.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | yes | MySQL connection |
| `DONOR_TOKEN_SECRET` | yes | HS256 key for donor JWTs; also the default key for code hashes |
| `ADMIN_TOKEN_SECRET` | yes | HS256 key for admin JWTs |
| `ADMIN_USERNAME` | yes | the one admin login name |
| `ADMIN_PASSWORD_HASH` | yes | PBKDF2 hash of the admin password (`generate_admin_hash.py` creates it) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | no | real email delivery; omit to run in demo mode |
| `SMTP_FROM`, `SMTP_FROM_NAME`, `SMTP_SSL` | no | sender address/name, implicit TLS for port 465 |
| `OTP_SECRET` | no | key for code hashes; falls back to `DONOR_TOKEN_SECRET` |
| `SIGNUP_CODE_TTL_MINUTES`, `RESET_CODE_TTL_MINUTES`, `OTP_RESEND_SECONDS`, `OTP_MAX_ATTEMPTS` | no | code lifetime, resend cooldown, attempt cap |

Setup of real mail delivery is documented in `backend/EMAIL_SETUP.md`
(Gmail app password, Brevo, Mailtrap, plus an error-decoding table).

---

## 4. Repository map

```
FoodShare/
├── PROJECT_HANDOFF.md          this document
├── .env                        secrets and configuration (not for sharing)
├── backend/
│   ├── main.py                 the whole API: config, auth, 31 endpoints
│   ├── database.py             connection helpers, schema creation and upgrade
│   ├── emailer.py              SMTP delivery + demo mode + delivery diagnostics
│   ├── requirements.txt        Python dependencies
│   ├── EMAIL_SETUP.md          how to switch on real email
│   ├── generate_admin_hash.py  prints a PBKDF2 hash for ADMIN_PASSWORD_HASH
│   ├── test_admin_api.py       manual smoke test of the admin endpoints
│   ├── test_db.py              runs initialize_database() on its own
│   └── venv/ , .venv/          two virtualenvs (see known issues)
├── html/                       one file per page (11 pages)
├── css/
│   ├── style.css               the entire design system, 21 numbered sections
│   └── vendor/                 Bootstrap Icons CSS + font, vendored locally
└── js/                         one shared module + one script per page
```

Frontend is classic browser JavaScript: no bundler, no modules, no framework.
Shared code attaches one global object, `App` (from `js/api.js`), and the code
input attaches `OtpInput`. Every file's logic is wrapped in
`(function () { "use strict"; … })();` so nothing else leaks to `window`.

---

## 5. Pages

| Page | Title | Scripts | What it does |
| --- | --- | --- | --- |
| `html/index.html` | FoodShare \| Share Food, Share Hope | api, food-card, home | landing page: hero with live count of available donations, how-it-works, current listings |
| `html/find-food.html` | Find Food \| FoodShare | api, food-card, find-food | searchable/filterable list of available donations + food request form |
| `html/track.html` | Donation Details \| FoodShare | api, track | donation detail by `?id=`: quantity, area, deadline, status, donor contact |
| `html/donate.html` | Donate Food \| FoodShare | api, donate | publishing form for a signed-in donor (with a sign-in prompt when anonymous) and a summary of their account |
| `html/contact.html` | Contact \| FoodShare | api, contact | contact form that builds a pre-filled email to the address in `CONTACT_EMAIL`, plus help text |
| `html/donor-register.html` | Create a Donor Account \| FoodShare | api, otp-input, donor-register | two-step signup: account details → 6-digit email code |
| `html/donor-login.html` | Donor Login \| FoodShare | api, donor-login | donor sign in, link to password reset |
| `html/forgot-password.html` | Reset Password \| FoodShare | api, otp-input, forgot-password | three-step reset: email → code → new password |
| `html/donor-dashboard.html` | Donor Dashboard \| FoodShare | api, donor-dashboard | publish form + own donations table + four statistics |
| `html/admin-login.html` | Admin Sign In \| FoodShare | api, admin-login | administrator sign in |
| `html/admin.html` | Admin Console \| FoodShare | api, admin | statistics, email-delivery panel, donations, donor accounts, food requests |

Navigation, the mobile menu, the active-link highlight and the footer year are
all handled by `js/api.js`, so no page repeats that code.

---

## 6. Shared frontend module — `js/api.js` (`App`)

| Export | Purpose |
| --- | --- |
| `request(path, options)` | `fetch` wrapper: JSON or form body, bearer token injection, `ApiError` with `.status`, `.isAuthError`, `.isNetworkError` |
| `getDonorToken` / `setDonorToken` / `clearDonorToken` | donor JWT in `localStorage` under `foodshare_donor_token` (survives refreshes) |
| `getAdminToken` / `setAdminToken` / `clearAdminToken` | admin JWT in `sessionStorage` under `foodshare_admin_token` (ends with the tab) |
| `formatDate`, `formatQuantity`, `toInputDateTime`, `toInputDate` | display and `<input>` formatting |
| `statusBadge`, `emptyState` | build the shared status pills and empty-state blocks |
| `showMessage`, `clearMessage` | consistent success/error/info banners |
| `pickList`, `debounce` | small utilities |

`js/otp-input.js` (`OtpInput.mount(container, options)`) builds the six
single-digit boxes used by both the signup and reset screens: auto-advance,
backspace to the previous box, arrow keys, paste of a whole code, and an
`onComplete` callback that submits the form when the sixth digit is typed.

`js/food-card.js` renders a donation card, used by both the landing page and
Find Food so a listing looks identical everywhere.

---

## 7. Design system

`css/style.css` is the single stylesheet, organised in 21 labelled sections.
Tokens are CSS custom properties on `:root`:

| Group | Values |
| --- | --- |
| Brand | `--primary #198754`, `--primary-dark #12653f`, `--primary-light #e9f7ef`, `--primary-soft #d7f0df`, `--accent #f4b942` |
| Surfaces | `--background #f6faf7`, `--surface #ffffff`, `--surface-sunken #eff7f1`, `--border #dce7df`, `--border-strong #cbd8ce` |
| Text | `--text #203027`, `--text-strong #14261b`, `--muted #68776e` |
| Status | `--success`, `--info`, `--warning`, `--danger`, `--neutral`, each with a `-soft` tint |
| Shape | `--radius-sm 8px`, `--radius 14px`, `--radius-lg 20px`; three shadow levels |
| Layout | `--container 1160px`, `--nav-height 74px`, system font stack |

Recurring components: `.panel`, `.card`, `.stat-card`, `.btn` (plus
`-secondary -danger -ghost -small -full -icon`), `.badge`, `.status-*` pills,
`.message-*` banners, `.form-group/.form-row/.form-help`, `.table-wrapper` +
`<colgroup>` fixed widths, `.toolbar` filters, `.steps/.step` progress trail,
`.otp-input/.otp-digit`, `.recipient`, `.resend-row`, `.demo-code`, `.link-btn`,
`.empty-state`, `.loading`, plus responsive rules at 1000/820/620/460 px.

Pages are responsive by reflowing grids rather than by hiding content; tables
live inside `.table-wrapper` (horizontal scroll contained) with fixed column
widths so the console never scrolls the whole page.

Accessibility: one `<h1>` per page, `<label for>` on every input, `aria-live`
message regions, `aria-current="page"` on the active nav link, `aria-label` on
icon-only buttons, `aria-hidden` on decorative icons, `:user-invalid` styling
instead of eager red borders, and a `prefers-reduced-motion` guard.

---

## 8. Data model (live MySQL schema)

Seven tables. Six are used by the application; `registrations` is legacy.

### `donor_accounts`
`id` BIGINT PK · `full_name` · `account_type` ENUM(Individual, Organization) ·
`organization_name` · `email` (unique) · `phone` · `location` ·
`password_hash` · `verification_status` ENUM(Pending, Verified, Rejected) ·
`email_verified` TINYINT(1) · `email_verified_at` · `password_changed_at` ·
`created_at`

### `donations`
`id` PK · `donor_id` (nullable, links to the donor account) · `food_name` ·
`item_type` ENUM(Food, Drink) · `quantity` DECIMAL(10,2) · `quantity_unit` ·
`category` · `location` · `pickup_deadline` DATETIME · `donor_name` ·
`contact` · `expiry_date` DATE · `packaging_condition` ·
`status` ENUM(Available, Reserved, Completed, Expired, Cancelled) ·
`created_at` · `updated_at`

`donor_name` and `contact` are copied onto the donation when it is created, so
a listing still shows who to call even if the account later disappears.

### `food_requests`
`id` PK · `requester_name` · `contact` · `location` · `food_category` ·
`quantity_needed` DECIMAL(10,2) · `request_status`
ENUM(Pending, Approved, Fulfilled, Rejected) · `created_at`

### `pending_registrations`
A signup waiting for its code. `email` unique · `full_name` · `account_type` ·
`organization_name` · `phone` · `location` · `password_hash` · timestamps.
The real `donor_accounts` row is created only when the code is entered.

### `email_otps`
`id` PK · `email` · `purpose` ENUM(signup, reset) · `code_hash` CHAR(64) ·
`attempts` · `expires_at` · `consumed_at` · `created_at`, indexed on
(email, purpose, consumed_at) and on `expires_at`. Only a keyed hash of the
code is stored — never the code itself.

### `admin_accounts`
`id` · `username` · `password_hash` · `created_at`. **Unused**: the single
administrator is configured in `.env`, not in this table.

### `registrations` (legacy, unused)
`full_name` · `role` · `organisation` · `location` · `email` · `phone` ·
`created_at`. No code reads or writes it. It can be dropped once its data is
no longer wanted.

### Schema drift — important

`database.py` creates tables with `CREATE TABLE IF NOT EXISTS`, which does not
alter a table that already exists. The live database was created by an earlier
version, so it differs from the declared definitions:

| Column | Declared in `database.py` | Actually in the live DB |
| --- | --- | --- |
| `donations.food_name` | VARCHAR(200) | VARCHAR(150) |
| `donations.item_type` | VARCHAR(100) | ENUM('Food','Drink') |
| `donations.quantity_unit` | VARCHAR(30) | VARCHAR(20) |
| `donations.location` | VARCHAR(255) | VARCHAR(250) |
| `donations.donor_id` | INT with FK `ON DELETE SET NULL` | BIGINT, **no foreign key** |
| `donor_accounts.id` | INT | BIGINT |
| `donor_accounts.email` | VARCHAR(255) | VARCHAR(254) |
| `donor_accounts.organization_name` | VARCHAR(200) | VARCHAR(150) |
| `donor_accounts.location` | VARCHAR(255) | VARCHAR(250) |

Consequences: the live DB has **no foreign keys** (deleting a donor leaves
`donor_id` dangling rather than nulling it), **no indexes on `donations`**
beyond the primary key, and a long food name or organization name that the API
accepts can be rejected by MySQL as "Data too long". Enum columns are the only
thing the startup sync repairs.

A fresh database created from `database.py` does not have these problems.

---

## 9. API reference

Base URL `http://127.0.0.1:8000`. JSON in and out, except `POST /donors/login`
which is form-encoded (OAuth2 password flow, so the Swagger authorise button
works). Auth is `Authorization: Bearer <token>`; a raw token without the
`Bearer ` prefix is also accepted.

### Public

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | service banner + link to `/docs` |
| GET | `/health` | `{status, database, email}`, where `email` is `smtp` or `demo` |
| GET | `/donations` | available listings; filters `category`, `location`, `search` (food name or type) |
| GET | `/donations/{id}` | one donation, including the donor contact number |
| POST | `/food-requests` | submit a food request |
| POST | `/donors/register` | step 1 of signup: park the details, email a code |
| POST | `/donors/register/verify` | step 2: exchange the code for an account and a token |
| POST | `/donors/register/resend` | new signup code for a pending registration |
| POST | `/donors/login` | email + password → donor JWT |
| POST | `/donors/password/forgot` | email a reset code (same reply whether the account exists or not) |
| POST | `/donors/password/verify-code` | code → 15-minute reset token |
| POST | `/donors/password/reset` | reset token + new password |
| POST | `/admin/login` | username + password → admin JWT |

### Donor (bearer token, only their own records)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/donors/me` | the signed-in account |
| GET | `/donors/me/donations` | their donations, newest first |
| GET | `/donors/me/dashboard` | `donor` + `summary` counts (total, available, reserved, completed, cancelled, expired) |
| POST | `/donations` | publish a donation; deadline must be in the future |
| PUT | `/donors/me/donations/{id}` | edit, only while status is Available |
| PATCH | `/donors/me/donations/{id}/complete` | mark collected (Available or Reserved) |
| PATCH | `/donors/me/donations/{id}/cancel` | cancel (Available only) |

Ownership is enforced in the SQL (`WHERE id = %s AND donor_id = %s`), so
another donor's id answers 404 rather than 403 and leaks nothing.

### Admin (bearer token)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/admin/donations` | every donation with the owner's email and verification state |
| PATCH | `/admin/donations/{id}/status` | set any of the five statuses |
| PATCH | `/admin/donations/{id}/complete` | convenience complete, idempotent |
| DELETE | `/admin/donations/{id}` | delete a record |
| GET | `/admin/donors`, `/admin/registrations` | donor accounts (same payload under two keys for older callers) |
| PATCH | `/admin/organizations/{id}/verification` | Pending / Verified / Rejected; 400 for individual accounts |
| GET | `/food-requests` | all requests |
| PATCH | `/food-requests/{id}/status` | Pending / Approved / Fulfilled / Rejected |
| GET | `/admin/mail/status` | delivery mode, host, sender, transport, missing settings, human-readable reason — never the password |
| POST | `/admin/mail/test` | send one real test message; 400 in demo mode, 502 if the provider refuses |

### Derived behaviour worth knowing

- **Expiry sweep.** `expire_stale_donations()` runs before every read of
  donations (public list, donor lists, admin list) and moves anything past its
  pickup deadline or expiry date to `Expired`, so no view disagrees.
- **Timezones.** Browsers send ISO strings in UTC; `to_server_local()` converts
  them to naive server-local time before comparison and storage, because
  MySQL's `NOW()`/`CURDATE()` are local. Without it, deadlines shifted by the
  machine's UTC offset.
- **Contact details.** A donation copies the donor's `full_name` and `phone`
  at creation; `donor_name`/`contact` in the request body are optional.

---

## 10. Authentication and security model

- Passwords: PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte random salt,
  stored as `pbkdf2_sha256$<salt>$<hex>`; verified with `hmac.compare_digest`.
- JWTs: HS256, 12-hour expiry, a `type` claim (`donor` / `admin` / `reset`) that
  is checked against the endpoint's expectation, so a donor token can never
  open an admin route. The admin subject must equal `ADMIN_USERNAME`.
- Email codes: 6 digits from `secrets.randbelow`, stored as
  `HMAC-SHA256(secret, "purpose:email:code")`. Single use, 15-minute lifetime,
  5 wrong attempts per code, 60-second resend cooldown (HTTP 429), pending
  signups purged after 24 hours. A database dump cannot be turned back into
  working codes without the application secret.
- Password reset: the code is exchanged for a 15-minute reset token, so the
  code is never resent alongside a new password. Saving the password writes
  `password_changed_at`, and `get_current_donor` rejects any token issued
  before that moment — a reset signs out other browsers. Reusing the current
  password is refused.
- Account enumeration: `/donors/password/forgot` replies identically for known
  and unknown addresses. `/donors/register` still answers 409 for an existing
  email (a signup has to say that).
- Not implemented: rate limiting on `/donors/login` or `/admin/login`, CSRF
  tokens (bearer headers make it low risk), refresh tokens, 2FA for donors.
  JWTs live in `localStorage`/`sessionStorage`, so an XSS bug would expose them.

---

## 11. Email subsystem

`backend/emailer.py` has two modes, chosen by configuration at startup:

- **`smtp`** — `SMTP_HOST` (plus `SMTP_USER`/`SMTP_PASSWORD` when the server
  needs a login) is set. Mail goes out over STARTTLS on 587, or implicit TLS on
  465 with `SMTP_SSL=true`. Uses only the standard library (`smtplib`,
  `email.message`), so there is nothing extra to install.
- **`demo`** — no configuration. Nothing is emailed: the code is printed in the
  API console and returned to the page as `dev_code`, so the whole flow can be
  demonstrated without a mailbox. The signup and reset screens show it in a
  "Demo mode" box. `dev_code` is never returned once SMTP is configured.

Email content is multipart (plain text + a small branded HTML body) with
purpose-specific subjects, and the message states the expiry and that the code
works once. A failed send raises `EmailDeliveryError`, which the API turns into
HTTP 502 with the provider's reason; the code row is deleted so the donor can
retry immediately instead of waiting out the cooldown.

The admin console shows the current mode, server, sender and encryption, names
any missing setting, and can send a test message — the quickest way to confirm
real delivery without going through a signup.

---

## 12. Feature inventory

**Public** — live count of available donations, filter by category/area/keyword,
donation cards, detail page with donor contact, food request form, contact page.

**Donor** — verified signup, sign in, password reset by email code, publish a
donation (type, category, quantity + unit, area, pickup deadline, optional
expiry and packaging), edit while available, mark collected, cancel, and four
statistics that always match the table beneath them. Pages reload their data
when the tab regains focus, so a change made elsewhere appears without a
refresh.

A donation can be published from two places: the dedicated `donate.html` page
(which shows a sign-in prompt when nobody is signed in and enforces a pickup
time at least 15 minutes ahead) and the panel on the donor dashboard. Both post
to the same endpoint with the same field set.

**Admin** — six summary cards, inline status changes, delete, organization
approve/reject, food-request status, search and status/type filters, and the
email-delivery panel.

**Cross-cutting** — automatic expiry, server-local time handling, shared error
messages that never show a raw stack trace, navigation that marks the current
page and switches to "Sign out" when a donor is signed in, and a `null`-origin
plus any-localhost-port CORS policy for local development.

---

## 13. Verification performed

Testing was done with throwaway scripts outside the repository; nothing in this
section is committed as an automated suite.

| Area | What was exercised | Result |
| --- | --- | --- |
| API (email codes) | wrong code, used code, expired code, attempt exhaustion, resend cooldown, replayed signup, tampered reset token, short password, reuse of current password, session invalidation after reset, unknown-address reset | 31 checks, all pass |
| API (edge cases) | organization signup stays Pending until approved, admin sees and approves it, mixed-case email, resubmission during cooldown, no account enumeration | 18 checks, all pass |
| Mail controls | status endpoint, test send refused in demo mode with a reason, admin token required, exact SMTP conversation (connect → EHLO → STARTTLS → login → MAIL FROM/RCPT TO → message), a real refused connection surfacing as an error | 20 checks, all pass |
| Browser flows | real Chrome via DevTools protocol: fill the signup form, receive and type the code, land on the dashboard signed in; reset the password in three steps, old password rejected, new password accepted | 33 checks, all pass |
| Rendered screens | static review of every step (signup step 2, reset steps 2 and 3, admin delivery panel) with screenshots | layout matches the design system |
| Static wiring | every `getElementById`/selector a page's scripts use exists in its HTML; every referenced script file exists; no page drives the code input without loading it | passes |
| Regression | full API suite re-run after each change; `/health` reports the delivery mode | passes |

The one bug this caught in production code: `html/index.html` never loaded
`js/food-card.js`, so the landing page's listing section always showed an error
state. Fixed.

---

## 14. Known issues and suggested next steps

Ordered by how much they matter.

1. **No version control.** The folder is not a git repository, so there is no
   history and no backup. `git init` plus a `.gitignore` covering `venv/`,
   `.venv/`, `__pycache__/`, `*.db` and `.env` is the first thing to do.
2. **Schema drift (section 8).** Bring the live database in line with
   `database.py` — ideally with a small migration script rather than dropping
   data: widen the drifted columns, convert `item_type` to the declared type,
   add the missing foreign key on `donations.donor_id`, and add indexes on
   `status`, `category` and `donor_id`. Until then long inputs can fail with
   "Data too long" and deleting a donor leaves dangling ids.
3. **No real email yet.** Set the SMTP values (see `backend/EMAIL_SETUP.md`)
   and restart. Until then codes are shown on screen, which is fine for a demo
   and unacceptable for real users.
4. **Leftover files.** `backend/` contains a zero-byte file named `` ` ``, an
   empty `database.p`, a stray SQLite artifact `foodshare.db`, and two
   near-duplicate helpers `generate_admin_hash.py` / `make_admin_hash.py`.
   `registrations` is an unused legacy table. Two virtualenvs exist
   (`venv/` and `.venv/`); keep one.
5. **Dependency list is stale.** `requirements.txt` declares `slowapi` and
   `pwdlib[argon2]`, neither of which is imported (the code uses `hashlib`), and
   omits `requests`, which `test_admin_api.py` needs.
6. **No login rate limiting.** Codes are throttled; passwords are not. A simple
   per-IP counter on the two login endpoints would close that.
7. **No pagination.** The admin console loads every donation, account and
   request at once. Fine at this size, awkward at a few thousand rows.
8. **Legacy donations are detached.** Rows created before donor accounts were
   linked have `donor_id = NULL`; they still appear publicly and in the admin
   table, but no donor dashboard owns them.
9. **Pre-existing accounts are grandfathered.** Accounts created before email
   verification existed have `email_verified = 1` by column default. Nobody was
   locked out, but their addresses were never actually confirmed.
10. **Food requests are one-way.** A request is stored and triaged; matching
    donors are not notified and nothing links a request to a donation.
11. **Admin is a single env-defined account.** The `admin_accounts` table is
    unused, so adding a second administrator needs code, not data.
12. **Tokens live in web storage.** Moving them to `HttpOnly` cookies would
    remove the XSS exposure, at the cost of CSRF handling and a small rewrite of
    `js/api.js`.
13. **The contact address is a placeholder.** `js/contact.js` has
    `CONTACT_EMAIL = "contact@foodshare.example"`, which the contact form turns
    into a `mailto:` link. Replace it with a real inbox before anyone relies on
    that page.
14. **The publish form exists twice.** `js/donate.js` and the panel in
    `js/donor-dashboard.js` build the same field set independently; a shared
    "donation form" module would remove the duplication and the risk of the two
    drifting apart.

---

## 15. Conventions in use

- Python: 4-space indent, type-free but annotated where it helps, docstrings
  explaining *why* on every non-obvious helper, `HTTPException` for all error
  responses with human-readable `detail`, narrow `try/except` and never a bare
  `except` without a comment explaining why swallowing is safe.
- SQL goes through three helpers (`fetch_one`, `fetch_all`, `execute_query`)
  that each open and close their own connection, always parameterised.
- JavaScript: `"use strict"`, one IIFE per file, `const`/`let`, no classes, DOM
  built with `document.createElement` rather than HTML strings, all user-facing
  text routed through `App.showMessage`.
- CSS: BEM-ish flat classes, shared tokens instead of literal colours, one
  section per component family, mobile rules grouped at the bottom.
- Naming: `donor` for account holders, `donation` for a listing, `request` for a
  food request; status values are capitalised exactly as the enums define them.

---

## 16. Suggested prompt when handing this to an assistant

> FoodShare is a food-donation web app: a FastAPI + MySQL JSON API
> (`backend/main.py`, `database.py`, `emailer.py`) and a static
> HTML/CSS/JavaScript frontend (`html/`, `css/style.css`, `js/`) with no build
> step. Donors sign up with a 6-digit email code, publish donations with a
> pickup deadline, and manage them from a dashboard; an admin console changes
> statuses, verifies organizations and triages food requests. Read
> PROJECT_HANDOFF.md for the schema, every endpoint, the security model and the
> known issues. Keep the existing conventions: design tokens in `style.css`,
> shared helpers in `js/api.js`, parameterised SQL through the three helpers in
> `main.py`, and messages through `App.showMessage`. [Then state the task.]
