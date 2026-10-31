# Demo script (about 7 minutes)

Prepare once:

```
python manage.py migrate
python manage.py seed_demo_data --reset-demo      # about 30 seconds: 10,000 subscriptions, ~32,000 invoices
python manage.py runserver
```

Logins (password for all: `Demo!Pass123`): `admin@demo.example`, `staff@demo.example`, `customer00001@demo.example`.
Use a second private window for the customer.

| Min | Show | What to say |
|---|---|---|
| 0:00 | Sign in as admin. The **dashboard**. | "Ten thousand subscriptions, every figure is a database aggregate, and it loads in under 100 ms." |
| 0:45 | **Reports**: change the period, filter one plan, open Export, download Invoices. | "MRR normalises every plan to a month. Exports are streamed and safe against spreadsheet formula injection." |
| 1:45 | **Subscriptions**: filter Active, search a company. Open one. | "Status stepper, history, lines, discount and tax breakdown. Prices are snapshots." |
| 2:30 | **New subscription** from the *Starter pack* template. Send quotation, Confirm, Activate. | "The plan decides which buttons exist. Confirm lists every problem at once. Try it with a plan minimum quantity of 9." |
| 3:30 | **Billing**: it shows subscriptions due today. Run billing. Run it again. | "Nothing is billed twice: row locks, an existence check, and a unique constraint in the database." |
| 4:15 | Open a new **invoice**: Print view, Send to customer (email prints in the terminal). Record a part payment, then the rest. | "The invoice turns Paid by itself. Overdue is never stored; it is derived." |
| 5:00 | Void a payment (admin only). | "Nothing is ever deleted. Voiding reopens the invoice and is audited." |
| 5:30 | Customer window: **portal** dashboard, My invoices, one invoice. Try `/staff/` and `/reports/`. | "Customers only ever see their own rows; every back-office page is closed to them." |
| 6:15 | Staff window: no Staff link, `/staff/` gives 403. In the Django admin open **Audit logs**. | "Roles are checked in the views and again in the services." |
| 6:45 | Terminal: `python manage.py perf_check --billing`, then `python manage.py test` (or show the last result). | "Every page is under the 2 second target with room to spare; the tests include parallel billing and parallel payments." |

If you are asked "what would you do next": background worker for billing, credit notes and proration, online payments
in the portal, split payments across invoices.
