# Walkthrough: using the site, step by step

Open http://127.0.0.1:8000 after `python manage.py runserver`. Use two browser windows: your normal one for the
admin, and a private (incognito) window for the staff member or customer, so the sessions do not mix.

## 0. First run
1. `python manage.py migrate` (creates the tables and the four default payment terms).
2. `python manage.py createsuperuser` (email, full name, password). This account has the role **Admin**.
3. `python manage.py runserver`.
4. Sign in at `/accounts/login/`. Too many wrong attempts (10 in 5 minutes) show a "Too Many Requests" page; wait and retry.

## 1. Accounts (step 2)
| What | Path | Notes |
|---|---|---|
| Customer sign-up | `/accounts/signup/` | Password: more than 8 characters with upper case, lower case and a special character, e.g. `Str0ng!Pass`. Signing up creates a **Customer** and signs them in. |
| Sign in | `/accounts/login/` | Email + password. |
| Change password | `/accounts/password-change/` | Also in the user menu (top right). |
| Forgot password | `/accounts/password-reset/` | The email is **printed in the terminal** running `runserver`. Open the link in it (`/accounts/reset/...`). It works once and expires after 1 hour. |
| Sign out | user menu, top right | |
| Django admin | `/admin/` | For the superuser. Useful to read the **Audit logs** (`/admin/core/auditlog/`). |

## 2. People and access (step 3)
1. As admin, open **Staff** (`/staff/`), click **Add staff member** (`/staff/new/`). Fill name and email, click **Generate strong password**, then **Copy**, then save.
2. In the private window sign in as that staff member. The sidebar shows Customers, Subscriptions, Products, Plans, Templates, Taxes and Discounts but **no Staff**. Opening `/staff/` shows the 403 page.
3. Back as admin, **Deactivate** the staff member on `/staff/`; their window is signed out at once. **Reactivate** brings them back.
4. **Customers** (`/customers/`, admin and staff) lists everyone who signed up; search by name, email or company.
5. A customer sees only the Dashboard; every back-office page gives 403.

| Role | Can do |
|---|---|
| Admin | Everything, including staff, products, plans, taxes, discounts, templates |
| Staff | View the catalog, taxes, discounts and templates; create and run subscriptions |
| Customer | Own portal only (arrives in a later step) |

## 3. Taxes, products, variants, plans (step 4), in this order
1. **Tax** `/taxes/new/`: name `GST 18`, type Percentage, rate `18`. In the **Tax calculator** card on `/taxes/` type `1000`; it shows ₹180.00 and a total of ₹1,180.00.
2. **Product** `/catalog/products/new/`: `CRM Suite`, Service, sales price `1000`, cost `400`, default tax `GST 18`. A second product `Helpdesk`, sales `500`, cost `100`, no tax.
3. **Variant**, on the CRM Suite page (`/catalog/products/<id>/`), "Add a variant": attribute `Brand`, value `Odoo`, extra price `560`. The row shows a final price of ₹1,560.00. Deactivate and Reactivate it with the buttons.
4. **Plan** `/catalog/plans/new/`: `Monthly Basic`, Monthly, minimum quantity 1, tick Closable, Pausable and Renewable. Try an end date before the start date, or Auto-close without an end date, to see the validation.
5. Staff members can open all of these pages but have no Add, Edit or Deactivate buttons.

## 4. Discounts (step 5)
1. `/discounts/new/`: name `Ten off`, Percentage `10`, applies to **Whole subscription**, minimum purchase `2000`, usage limit `100`.
2. Another: `CRM promo`, Fixed amount `300`, applies to **Selected products** (the product box appears), choose CRM Suite.
3. Status badges on `/discounts/`: active, scheduled (future start), expired (past end), exhausted (limit reached), inactive.
4. Rule: discounts never stack. When several qualify, the one that saves the most is used.

## 5. Quotation templates and subscriptions (step 6)
1. **Template** `/quotation-templates/new/` (admin): `Starter pack`, plan `Monthly Basic`, valid for 30 days. On its page add `CRM Suite (Brand: Odoo)` x 1 and `Helpdesk` x 2.
2. **Subscription** `/subscriptions/new/`: customer (Cara), template `Starter pack`, payment term `30 days`, start date today. The plan and lines are copied.
3. On the subscription page (`/subscriptions/<id>/`):
   - Lines: CRM Suite (Brand: Odoo) 1 x ₹1,560.00 with GST 18, Helpdesk 2 x ₹500.00.
   - Subtotal ₹2,560.00, Discount **Ten off** -₹256.00 (shared -₹156.00 and -₹100.00), Tax ₹252.72, **Total per period ₹2,556.72**. Tax is charged after the discount.
   - "Add a product" adds more lines; adding the same product again raises its quantity. Remove deletes a line.
4. Status buttons (top right), in order: **Send quotation** (Draft to Quotation), **Confirm**, **Activate** (sets Next billing to the start date), **Pause** and **Resume** (only if the plan is pausable), **Close** (only if the plan is closable). The History box records each move and who made it.
5. Confirm is refused, with all reasons listed, if there are no lines, a line is below the plan minimum quantity, the plan is inactive or outside its dates, the quotation has expired, or the customer is deactivated.
6. After Confirm the header and lines are locked. **Renew** (active or closed, renewable plan) creates a new draft at today's prices.
7. `/subscriptions/` lists everything: filter by status, search by number, customer or company.

## 6. Billing and invoices (step 7)
1. Make sure a subscription is **Active** (see section 5). On activation its Next billing date is its start date, so it is due today.
2. As admin open **Billing** (`/billing/`). It lists what is due today. Click **Run billing now** and confirm.
3. The result summary shows the invoices created, the total billed, anything closed or stopped, and any failures with the reason. It is shown once.
4. Click **Run billing now** again: 0 invoices are created. A period is never billed twice.
5. **Invoices** (`/invoices/`, admin and staff): filter by Draft, Confirmed, Overdue, Paid or Cancelled; search by invoice number, subscription or customer.
6. Open an invoice (`/invoices/<id>/`): customer, plan, period, due date (issue date plus the payment term), lines with discount and tax, totals, and the balance due.
   - **Print view** opens a clean page; use its Print button.
   - **Send to customer** emails the summary. In development the email prints in the terminal running `runserver`.
   - **Cancel invoice** (admin, and only while nothing is paid). The period stays billed and will not be invoiced again.
7. Go back to the subscription page: the new invoice is listed under **Invoices**, and Next billing has moved on by one period.
8. Overdue is never stored: a confirmed invoice with an unpaid balance and a due date in the past shows as **overdue** automatically.
9. From the terminal (for a cron job): `python manage.py run_billing`. Options: `--dry-run` lists what is due without changing anything; `--date 2026-12-31` bills as if that were today.
10. End of a subscription: if its expiry date is reached and the plan has **Auto-close**, the system closes it after the last period (history note "Reached end date"). Without Auto-close, billing simply stops and the subscription stays Active until someone closes it.

## 7. Payments (step 8)
1. Open an invoice (`/invoices/<id>/`) that is **Confirmed** and click **Record payment** (`/payments/record/<invoice id>/`). Admin and staff can do this.
2. The form shows the invoice total, what is already paid and the balance due. Amount defaults to the balance. Choose a method (**Cash, Bank transfer, Card, UPI**), set the date (today or earlier) and add a reference such as a cheque number or UPI reference.
3. **Part payment:** enter less than the balance, for example 500 of 1,180. The invoice stays Confirmed and shows the new balance (₹680.00). Record more payments until the balance is zero.
4. When the payments add up to the total, the invoice turns **Paid** automatically. The Record payment button disappears.
5. Rules you can try: paying more than the balance is refused, zero or negative amounts are refused, a future date is refused, and paid or cancelled invoices cannot receive payments.
6. The invoice page lists every payment under **Payments** with date, method, reference and who recorded it.
7. **Void** (admin only, next to each payment): enter a reason. The payment is struck through, the invoice balance goes back up and a Paid invoice is reopened. Voided payments never count as collected. Nothing is ever deleted.
8. **Payments** (`/payments/`, admin and staff) shows three cards: **Outstanding** (what is still owed on open invoices), **Overdue** (open invoices past their due date, click it to see them) and **Collected this month**. Below is every payment; filter by method or search by invoice, customer or reference.
9. An invoice that is fully discounted (total 0) is created as Paid and is never outstanding.
10. Cancelling: an invoice that has a payment cannot be cancelled. Void the payment first.

## 8. Dashboard and reports (step 9)
1. Sign in as admin or staff. The home page (`/`) is now the business dashboard: **Monthly recurring revenue**, **Invoiced** and **Collected** for this month, **Outstanding** and **Overdue** as of today, subscription counts by status, **Collected per month** (last 6 months), **Payments by method** and the **Overdue invoices** list with days overdue.
2. Click **Full reports** (or **Reports** in the sidebar, `/reports/`).
3. Filters at the top: **Period** (This month, Last 30 days, This quarter, This year, All time, Custom with From and To dates) and **Plan**. Click **Apply**. A bad custom range shows a message and falls back to this month.
4. Check the maths with the data from the walkthrough: a monthly subscription with lines of ₹2,560.00 gives MRR ₹2,560.00. A yearly plan is divided by 12, weekly is multiplied by 52/12, daily by 365/12. MRR counts Active subscriptions only, before discounts and tax.
5. **Recurring revenue by plan** shows the per-period amount and the monthly figure for each plan.
6. **Export to CSV**: Invoices, Payments, Subscriptions, Overdue invoices. The files use the period and plan you selected (overdue is always as of today) and open in Excel. Each export is written to the audit log.
7. Staff can see the dashboard, reports and exports too; only admin runs billing and voids payments.

## 9. Customer portal (step 9)
1. In a private window sign in as a customer (for example the one who signed up in section 1). The home page is the customer dashboard: **Active subscriptions**, **Next billing**, **Amount due**, **Overdue**, and short lists of subscriptions and invoices. No business figures are shown.
2. The sidebar has only **My subscriptions** (`/portal/subscriptions/`) and **My invoices** (`/portal/invoices/`).
3. A subscription page (`/portal/subscriptions/<id>/`) shows the plan, dates, products, discount, tax, the total per period and its invoices. It is read-only. Draft subscriptions are never shown; quotations are, with a note.
4. An invoice page (`/portal/invoices/<id>/`) shows the lines, totals, balance due and the payments received (voided ones are hidden). **Print or save as PDF** opens a clean page: use the browser's Print, then choose Save as PDF.
5. Filter invoices by Unpaid, Overdue or Paid, and search by number.
6. A customer can only ever open their own records. Another customer's id, or any back-office page, gives a 404 or the 403 page.

## 10. Demo data and checks (step 10)
1. `python manage.py seed_demo_data --reset-demo` loads about 10,000 subscriptions, 2,500 customers, 32,000 invoices and 25,000 payments in about 30 seconds, then checks that every invoice, line and payment adds up. Use `--subscriptions 500` for a small set. The same `--seed` always produces the same data.
2. Sign in with `admin@demo.example`, `staff@demo.example` or `customer00001@demo.example`; the password for all is `Demo!Pass123`.
3. `/billing/` now shows about 185 subscriptions due today. Run billing to see a large run, then run it again to see that nothing is billed twice.
4. `python manage.py verify_data` checks the books at any time: invoice totals against their lines, `amount_paid` against the payments, statuses, discount usage and subscription dates.
5. `python manage.py perf_check --billing --explain` times every main page and the billing run, and prints the query plans. See `docs/PERFORMANCE.md`.
6. `--reset-demo` removes only the `@demo.example` customers and their data, never anything else.
