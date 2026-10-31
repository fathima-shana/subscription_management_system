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
