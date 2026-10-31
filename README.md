# Brightloop Subscription Management System

Django + PostgreSQL + HTMX/Alpine/Tailwind. Modular monolith (`apps/<module>/{models,services,selectors,views}`).

## Run locally (Windows)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env      # then set DB_PASSWORD
python manage.py runserver
```

Create the database first: `createdb -U postgres brightloop` (or via pgAdmin).

## Accounts (step 2)

```
python manage.py migrate
python manage.py createsuperuser      # email, full name, password; gets role "admin"
python manage.py test                 # needs CREATE DATABASE rights on your postgres user
```
Sign up at `/accounts/signup/`. Password reset emails print in the terminal running `runserver` (console backend).

## People and access (step 3)

| Role | Can do |
|---|---|
| Admin | Everything, including adding/deactivating staff (`/staff/`) |
| Staff | Customer directory (`/customers/`) and, in later steps, day-to-day billing work |
| Customer | Only their own subscriptions and invoices (portal) |

Access rules live in `apps/core/permissions.py` (roles) and `apps/core/scoping.py` (own-rows-only for customers).
Sensitive actions are recorded in the audit log (`core.AuditLog`, read-only in Django admin).

## Catalog and taxes (step 4)

| Page | URL | Admin | Staff |
|---|---|---|---|
| Products (with variants) | `/catalog/products/` | create, edit, activate/deactivate | view |
| Plans | `/catalog/plans/` | create, edit, activate/deactivate | view |
| Taxes + calculator | `/taxes/` | create, edit, activate/deactivate | view |

Design notes:
- Money is `Decimal`, rounded half-up to 2 places in one place (`apps/core/money.py`).
- Tax rules (`apps/taxes/calculator.py`): tax is computed on the amount after discounts; several taxes use the same base (no compounding); a fixed tax is a flat amount per line.
- Final variant price = product sales price + variant extra price.
- Products, plans, taxes and variants are deactivated, never deleted, so later invoices can always refer to them.
- Names are unique ignoring case, enforced by the database as well as the forms.
- Every change is written to the audit log with before/after values.

## Discounts (step 5)

`/discounts/`: admin creates and edits, staff view. Discount rules live in `apps/discounts/engine.py`:
- Applies only when active, inside its date range (inclusive), not used up, and the quantity and amount it covers reach the minimums.
- "Selected products" discounts look only at lines of those products; "Whole subscription" discounts look at every line. Amounts are before tax.
- Percentage = base x value / 100. Fixed = the value, capped at the base.
- No stacking: the eligible discount with the biggest saving wins; ties go to the older one.
- `services.redeem()` uses up one redemption with a single guarded UPDATE, so parallel invoices can never pass the usage limit (a database CHECK backs it up). The billing engine (step 7) calls it.
- `engine.explain()` returns the reason each discount was skipped, for debugging.

## Subscriptions (step 6)

`/subscriptions/` (admin and staff) and `/quotation-templates/` (admin edits, staff view).

- Status flow: Draft, Quotation, Confirmed, Active, Closed, plus Paused. One table of allowed moves in `apps/subscriptions/workflow.py`; every move is locked, checked, written to the status history and the audit log.
- Plan rules drive the buttons: Pause needs `pausable`, early Close needs `closable`. Automatic closing at the end date (step 7) bypasses `closable`.
- Confirming checks: at least one line, minimum quantity per line, plan active and in its date window, quotation not expired, customer active. All problems are shown together.
- Pricing order (`apps/subscriptions/pricing.py`): discount first (best single discount, shared across lines in proportion), tax after. The billing engine reuses it, so previews match invoices.
- Line prices are snapshots; a later price change never alters an existing line.
- Numbers (`SUB-00001`) come from a row-locked counter in `apps/core/sequences.py`.
- Resuming a paused subscription never back-bills the paused time.
- Payment terms (Immediate, 15, 30, 45 days) are seeded by a migration; edit them in Django admin.
- A customer-scoped selector already exists (`selectors.subscription_queryset(user)`); the portal in step 9 will use it.

## Billing and invoices (step 7)

`/billing/` (admin: run billing), `/invoices/` (admin and staff), and `python manage.py run_billing [--dry-run] [--date YYYY-MM-DD]`.

Rules (`apps/billing/engine.py`):
- Due = Active and next billing date reached. Billing is in advance, one invoice per period, whole periods only (no proration).
- Each invoice is created in its own transaction together with moving the next billing date on and using up the discount, so a failure leaves nothing half-done.
- `SELECT ... FOR UPDATE SKIP LOCKED` on the subscription means two runs at once never bill the same subscription. `UNIQUE(subscription, period_start)` in the database is the last line of defence, and a period that already has an invoice only gets its date moved on.
- A subscription several periods behind is caught up one invoice per period (at most 24 per run). Monthly and yearly dates stay on the start day (31 Jan, 28 Feb, 31 Mar).
- Discounts valid on the first day of the billed period are considered; the best one that can still be redeemed is used.
- Invoices copy names, prices, tax rates and the discount, so later changes never alter them. A cancelled invoice keeps its period billed.
- After the last period: Auto-close plans are closed by the system; others stop billing and stay Active.
- One failing subscription never stops the run; it is logged and shown in the summary.
- Overdue is derived (confirmed, unpaid, past due date), never stored.
- Paying an invoice (and marking it Paid automatically) arrives with payments in step 8.
