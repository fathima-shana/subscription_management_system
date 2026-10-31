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
