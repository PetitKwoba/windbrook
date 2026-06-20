***

```markdown
# Windbrook Client Report Portal

A secure internal tool for the EF / Windbrook team to enter quarterly client financial data and
generate polished SACS (cashflow) and TCC (net worth) PDF reports — reducing meeting prep
from a full day to under an hour.

---

## Overview

The portal replaces a manual process that involved pulling balances from Pinnacle Bank,
Charles Schwab, Zillow, and RightCapital, then assembling reports in Canva and Word.
All math is automated. Reports are immutable snapshots stored with full download history.

**Stack:** Python · HTML/CSS/JS · SQLite · ReportLab · Railway

---

## Architecture

```
windbrook/
├── aw_portal/
│   ├── web.py          # Zero-dependency HTTP server (BaseHTTPRequestHandler)
│   ├── db.py           # SQLite connection, schema migrations
│   ├── security.py     # Sessions, CSRF, roles, password hashing
│   ├── calculations.py # SACS and TCC arithmetic (pure, deterministic)
│   ├── pdfs.py         # ReportLab PDF generation (SACS + TCC)
│   ├── excel.py        # Excel workbook export
│   └── static/
│       ├── app.css
│       └── app.js
└── README.md
```

**Request flow:** Browser → `web.py` (route + auth) → `calculations.py` → `pdfs.py` /
`excel.py` → binary response. No framework. No ORM. All SQL is written inline.

---

## Local Setup

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
python -m aw_portal
```

The server starts on `http://127.0.0.1:8000`. Default credentials are printed to stdout
on first boot. **Change them before deploying to production.**

---

## Environment Variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `HOST` | No | `127.0.0.1` | Set to `0.0.0.0` for Railway |
| `PORT` | No | `8000` | Railway injects this automatically |
| `RAILWAY_DATABASE_PATH` | No | `./windbrook.db` | Absolute path to SQLite file on persistent volume |
| `CANVA_API_KEY` | No | — | Reserved for V2 Canva export. Not used in V1. |

> **Production note:** Never run with default credentials. Set a strong admin password
> on first login and disable the dev credential hint in `web.py → render_login()`.

---

## Role Hierarchy

| Role | Level | Can Do |
|---|---|---|
| `system_admin` | 5 | Everything + cross-company visibility, Companies screen |
| `company_admin` | 4 | User management within their company |
| `planner` | 3 | Create/edit clients, manage accounts and profile data |
| `assistant` | 2 | Enter quarterly balances, generate and download reports |
| `viewer` | 1 | Read-only access to clients and reports |

---

## Key Domain Terms

| Term | Meaning |
|---|---|
| **SACS** | Simple Automated Cash Flow — one-page cashflow diagram (Inflow → Outflow → Private Reserve) |
| **TCC** | Total Client Chart — one-page net worth overview (retirement, non-retirement, trust, liabilities) |
| **Inflow** | Client's monthly take-home pay deposited into primary checking |
| **Outflow** | Agreed monthly expense budget transferred to spending account |
| **Private Reserve** | High-yield savings account where excess cash accumulates |
| **Floor** | $1,000 minimum balance maintained in each bank account as a buffer |
| **Trust** | Funded by primary residence; value pulled from Zillow Zestimate quarterly |

---

## Calculation Rules

All arithmetic lives in `calculations.py` and is fully deterministic — no AI, no rounding
ambiguity.

| Formula | Expression |
|---|---|
| Monthly Excess | `Inflow − Outflow` |
| Private Reserve Target | `(Outflow × 6) + sum(insurance_deductibles)` |
| Client 1 Retirement Total | `sum(balances where owner_index=1 AND category='retirement')` |
| Client 2 Retirement Total | `sum(balances where owner_index=2 AND category='retirement')` |
| Non-Retirement Total | `sum(balances where category='non_retirement')` — **trust excluded** |
| Grand Total Net Worth | `Client1_Retirement + Client2_Retirement + Non_Retirement + Trust` |
| Liabilities Total | Displayed separately — **never subtracted from net worth** |

> The liabilities-not-subtracted rule is intentional (Rebecca, transcript 26:15).
> The non-retirement total excluding trust is also intentional (Rebecca, transcript 24:28).

---

## Report Lifecycle

1. **Profile setup (one-time):** Enter client static data — names, DOB, SSN last 4,
   account structure, monthly salary, expense budget.
2. **Quarterly prep:** Click "Start Quarterly Report" from the client profile. Form
   pre-populates static data and shows last quarter's values as reference hints.
3. **Balance entry:** Enter current balances for each account (Schwab, Pinnacle Bank,
   Zillow home value, Private Reserve).
4. **Draft save:** "Save Draft" persists progress without generating. Resume any time.
5. **Generate:** "Generate Report" runs all calculations, stores an immutable
   `snapshot_json`, and redirects to the report view.
6. **Download:** SACS PDF, TCC PDF, and Excel workbook are available from the report
   view. All three can be re-downloaded indefinitely from report history.

---

## HTTP Endpoints

| Method | Path | Role Required | Description |
|---|---|---|---|
| GET | `/` | Any | Redirects to `/clients` |
| GET | `/login` | — | Login page |
| POST | `/login` | — | Authenticate user |
| POST | `/logout` | Any | End session |
| GET | `/clients` | Any | Client list with last report date |
| GET | `/clients/new` | planner | New client form |
| POST | `/clients` | planner | Create client |
| GET | `/clients/{id}` | Any | Client detail + report history |
| GET | `/clients/{id}?edit=1` | planner | Edit client form |
| POST | `/clients/{id}` | planner | Update client |
| GET | `/clients/{id}/reports/new` | assistant | Quarterly report entry form |
| POST | `/clients/{id}/reports/draft` | assistant | Save report draft |
| POST | `/clients/{id}/reports` | assistant | Generate final report |
| GET | `/reports` | Any | Full report history |
| GET | `/reports/{id}` | Any | Report detail with totals |
| GET | `/reports/{id}/sacs.pdf` | Any | Download SACS PDF |
| GET | `/reports/{id}/tcc.pdf` | Any | Download TCC PDF |
| GET | `/reports/{id}/excel.xlsx` | Any | Download Excel workbook |
| GET | `/users` | company_admin | User management |
| GET | `/companies` | system_admin | Tenant overview |
| GET | `/audit` | Any | Audit log (last 100 events) |
| GET | `/health` | — | `{"ok":true}` health check |

---

## Database Schema

SQLite managed via idempotent `CREATE TABLE IF NOT EXISTS` migrations in `db.py`.
The database file is stored on a Railway persistent volume — **do not delete the volume**.

Core tables: `companies`, `users`, `sessions`, `clients`, `client_people`,
`client_accounts`, `trust_assets`, `liabilities`, `deductible_items`,
`quarterly_reports`, `report_drafts`, `audit_events`, `schema_migrations`.

---

## Security Model

- **Passwords:** bcrypt hashed via `security.py → hash_password()`.
- **Sessions:** Random 32-byte hex tokens stored server-side in `sessions` table with
  expiry. HttpOnly + SameSite=Lax cookie.
- **CSRF:** Per-session token required on every POST. Validated before any mutation.
- **Tenant isolation:** All queries are scoped to `company_id`. Cross-company access
  raises `PermissionError` for all roles except `system_admin`.
- **SSN masking:** `mask_ssn_last4()` in `security.py` — SSN digits are never logged or
  included in audit events.
- **Immutable reports:** `snapshot_json` is written once and never updated. Edits to a
  client profile do not affect historical reports.

---

## V1 Scope Boundaries

**Intentionally excluded from V1 — do not build:**

- RightCapital API auto-pull (data is unreliable — Maryann, transcript 49:06)
- Schwab auto-pull (compliance restriction on credential sharing — Rebecca, transcript 48:14)
- Pinnacle Bank auto-pull (data arrives via secure email from personal bankers)
- Zillow API auto-pull (manual Zestimate entry is the current workflow)
- Canva export (confirmed as "nice-to-have"; PDF download is the primary output)
- Dropbox auto-save (mentioned but not committed — verify before adding)
- Monthly email distribution (team cadence is quarterly only)

All of the above are tracked as V2 candidates.

---

## Railway Deployment Checklist

- [ ] Set `HOST=0.0.0.0` in Railway environment
- [ ] Attach a persistent volume; set `RAILWAY_DATABASE_PATH` to its mount path
- [ ] Remove or gate the dev credential hint in `render_login()`
- [ ] Confirm `PORT` is injected by Railway (no need to set manually)
- [ ] Run the app once to trigger `init_db()` and bootstrap the system admin
- [ ] Set a production admin password immediately after first login
- [ ] Verify SACS and TCC PDFs against client sample documents before go-live
```

***

