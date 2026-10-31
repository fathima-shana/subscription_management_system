# Brightloop: Subscription Management System

A subscription billing system: catalog and plans, quotations, a subscription lifecycle, an idempotent billing
engine, invoices, payments, reports and a customer portal.

**Stack:** Django 5.1, PostgreSQL, HTMX, Alpine.js, Tailwind (CDN). One modular monolith, no external services.

| Read this | For |
|---|---|
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | A 7-minute demo, step by step |
| [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) | Every screen and path, in the order you would use them |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design, state machines, the billing engine, security, trade-offs |
| [docs/ERD.md](docs/ERD.md) | Database diagram and constraints |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Measured timings with 10,000 subscriptions and 30,000 invoices |
| [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md) | Branches and commits |

## Run it (Windows, VS Code terminal)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env          # then set DB_PASSWORD
createdb -U postgres brightloop # or create the database in pgAdmin
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000. The superuser gets the **Admin** role.
Password reset and invoice emails print in the terminal running `runserver` (console email backend).

### With demo data

```
python manage.py seed_demo_data --reset-demo
```

Loads about 10,000 subscriptions, 32,000 invoices and 25,000 payments in about 30 seconds and verifies them. Sign in as
`admin@demo.example`, `staff@demo.example` or `customer00001@demo.example` (password `Demo!Pass123`).

## Commands

| Command | What it does |
|---|---|
| `python manage.py test` | Runs the whole test suite (a few minutes; needs CREATE DATABASE rights for your PostgreSQL user) |
| `python manage.py run_billing [--dry-run] [--date YYYY-MM-DD]` | Bills every due subscription; safe to run repeatedly (cron-friendly) |
| `python manage.py seed_demo_data [--subscriptions N] [--reset-demo]` | Loads demo data |
| `python manage.py verify_data` | Checks that invoices, lines, payments, discounts and subscriptions add up |
| `python manage.py perf_check [--billing] [--explain] [--markdown]` | Times the main pages and the billing run |
| `python manage.py check --deploy` | Django's production security check (use `DJANGO_SETTINGS_MODULE=config.settings.prod`) |

The same commands are available as VS Code tasks (Terminal, Run Task).

## Features

| Area | What it does |
|---|---|
| Accounts | Sign-up, login, password reset (single use, 1 hour), password rules, admin-created staff, rate limits |
| Roles | **Admin** (everything), **Staff** (day-to-day: customers, subscriptions, invoices, payments, reports), **Customer** (own data only) |
| Catalog | Products with variants and extra prices, plans (daily, weekly, monthly, yearly) with minimum quantity, dates, auto-close, closable, pausable, renewable |
| Taxes | Percentage or fixed, calculator, applied after discounts |
| Discounts | Percentage or fixed, product or whole-subscription scope, date range, minimum purchase or quantity, usage limit; best single discount wins |
| Subscriptions | Quotation templates, Draft, Quotation, Confirmed, Active, Paused, Closed, history, renewals, plan-driven rules and guards |
| Billing | Idempotent engine (row locks plus a unique constraint), catch-up of missed periods, auto-close at the end date, button and command |
| Invoices | Snapshot of every price and rate, print view, email, cancel, derived overdue status |
| Payments | Cash, bank, card, UPI; part payments; invoices turn Paid automatically; void with audit; outstanding and overdue totals |
| Reports | MRR, active subscriptions, invoiced, collected, outstanding, overdue, payments by method, monthly trend; period and plan filters; CSV exports |
| Portal | Customers see their own subscriptions, invoices, payments and print view |
| Audit | Append-only log of sensitive actions, read-only in the Django admin |

## Roles at a glance

| | Admin | Staff | Customer |
|---|:-:|:-:|:-:|
| Staff accounts, products, plans, taxes, discounts, templates (create or edit) | yes | view | no |
| Customers, subscriptions, invoices, payments, reports | yes | yes | no |
| Run billing, cancel invoice, void payment | yes | no | no |
| Own subscriptions, invoices and payments | n/a | n/a | yes |

## Project layout

```
config/                 settings (base, dev, prod), root URLs
apps/
  core/                 audit, number sequences, money, permissions, scoping
  accounts/ taxes/ catalog/ discounts/ subscriptions/ billing/ payments/
  reports/ portal/      dashboards, exports, customer pages
  demo/                 seed data, integrity checks, performance check
templates/ static/      HTMX + Alpine + Tailwind pages
docs/                   design, ERD, performance, walkthrough, demo script
```

Every feature app has `models.py`, `services.py` (all writes), `selectors.py` (all reads), thin `views.py`, and `tests/`.

## Key design decisions

* **Money is `Decimal`** with one rounding rule (half-up, 2 places); invoices store copies of prices, rates and names so history never changes.
* **Nothing is billed twice:** row lock with `SKIP LOCKED`, an existence check, and `UNIQUE (subscription, period_start)`.
* **Derived, not stored:** overdue status and outstanding balances are computed from due dates and payments.
* **Rules live in one place:** the subscription state machine, the discount engine and the pricing function are pure and unit-tested; previews and invoices share them.
* **Defence in depth:** every view is role-checked and every service re-checks the role; customers are scoped to their own rows in one helper.

Known limits (no proration, no credit notes, read-only portal, synchronous billing run) and what comes next are listed in `docs/ARCHITECTURE.md`.
