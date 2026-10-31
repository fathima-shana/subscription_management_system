# Performance

Target: every main page responds in under 2 seconds with about 10,000 subscriptions and 30,000 invoices.

## How it was measured

```
python manage.py seed_demo_data --reset-demo      # 10,000 subscriptions, 32,602 invoices, 24,781 payments
python manage.py perf_check --markdown --runs 5 --billing --explain
```

`perf_check` signs in as an admin (and as a customer for the portal pages), loads each page in-process with Django's
test client, and reports the median of 5 runs plus the number of SQL queries. It exits with an error if any page is
slower than `--limit-ms` (default 2000) or does not return HTTP 200.

Environment of these numbers: 1 CPU core, PostgreSQL 16.15, Python 3.12, Django development settings, a freshly
loaded and `ANALYZE`d database. They exclude network time and the browser. **Run the command on your own machine and
paste your own table here before the demo**; absolute times differ from machine to machine, the shape does not.

## Results

| Page | Time (ms) | Queries | Size (KB) |
|---|---:|---:|---:|
| Dashboard | 75 | 14 | 22 |
| Reports (all time) | 82 | 15 | 27 |
| Reports (this month, one plan) | 78 | 16 | 26 |
| Subscriptions list | 18 | 6 | 24 |
| Subscriptions: status filter | 17 | 6 | 24 |
| Subscriptions: search by company | 44 | 6 | 24 |
| Subscriptions: deep page 333 | 57 | 6 | 24 |
| Invoices list | 10 | 6 | 25 |
| Invoices: overdue filter | 10 | 6 | 25 |
| Invoices: search by customer | 94 | 6 | 25 |
| Invoices: deep page 1086 | 39 | 6 | 25 |
| Payments list | 27 | 9 | 20 |
| Customers list | 9 | 6 | 17 |
| Customers: search | 12 | 6 | 17 |
| Billing (due list) | 7 | 6 | 14 |
| Subscription detail | 18 | 13 | 19 |
| Invoice detail | 8 | 7 | 15 |
| CSV export: invoices (all 32,602 rows) | 1159 | 6 | 5633 |
| CSV export: payments (all) | 1069 | 6 | 1948 |
| CSV export: subscriptions (all) | 692 | 6 | 1250 |
| Portal dashboard (customer with 49 invoices) | 16 | 10 | 11 |
| Portal subscriptions | 8 | 6 | 12 |
| Portal invoices | 8 | 6 | 18 |

All 23 pages are under 2000 ms; every interactive page is under 100 ms. Whole-database CSV exports are the slowest
operations (about one second) because they write 2 to 6 MB; they are streamed in chunks, so memory stays flat and the
first bytes arrive immediately.

Billing run (rolled back so nothing is saved): 185 due subscriptions billed in 2.5 s, about 75 invoices per second,
0 failures. Each invoice commits in its own transaction on purpose (one bad subscription never affects another).
A month-end run of 10,000 monthly subscriptions would therefore take a few minutes; a background worker is the next step.

## Why it is fast

* **No per-row queries.** Lists use `select_related` and annotated totals. Tests assert the query count does not grow
  with the number of rows (products, subscriptions, invoices, payments, reports).
* **Aggregates in the database.** MRR, invoiced, collected, outstanding, overdue, payments by method and monthly series
  are `SUM`/`COUNT ... GROUP BY` queries; the report page runs 15 queries whatever the size of the data.
* **Server-side pagination and filtering** (15 rows per page); deep pages use `OFFSET` and stay under 60 ms at this size.
* **Indexes that match the questions asked:**
  * `subs_due_idx`: partial index on `next_billing_date` where `status = 'active'` (the billing run's lookup)
  * `billing_due_status_idx` on `(due_date, status)` (overdue lists and totals)
  * indexes on invoice `status` and `customer`, payment `invoice` and `payment_date`, subscription `(customer, status)`
  * unique indexes on the numbers (`SUB-...`, `INV-...`)

## Query plans (`perf_check --explain`)

Subscriptions due for billing: a bitmap scan on the partial index, 0.24 ms for 185 rows.

```
Sort  (actual time=0.206..0.214 rows=185)
  ->  Bitmap Heap Scan on subscriptions_subscription  (actual time=0.028..0.144 rows=185)
        Recheck Cond: ((next_billing_date <= '2026-10-31') AND (status = 'active'))
        ->  Bitmap Index Scan on subs_due_idx  (actual time=0.013..0.013 rows=185)
Execution Time: 0.236 ms
```

Overdue invoices: the `(due_date, status)` index returns rows already ordered, 0.06 ms for the first 15.

```
Limit  (actual time=0.048..0.050 rows=15)
  ->  Incremental Sort  Sort Key: due_date, id  Presorted Key: due_date
        ->  Index Scan using billing_due_status_idx on billing_invoice  (actual time=0.016..0.034 rows=16)
              Index Cond: ((due_date < '2026-10-31') AND (status = 'confirmed'))
Execution Time: 0.064 ms
```

Invoice list, first page: primary-key scan in reverse with memoized joins, 0.16 ms. Subscription list (with the
annotated subtotal): a group aggregate that stops after 15 rows, under 1 ms.

## Limits and what would come next

* `icontains` search scans the table. It is 40 to 100 ms at this size; a trigram (`pg_trgm`) index would keep it flat at millions of rows.
* `OFFSET` pagination gets slower on very deep pages; keyset pagination would fix that.
* The report aggregates read live tables. For far larger data, a nightly summary table would keep the dashboard instant.
* The billing run is synchronous; move it to a worker with batches for very large runs.
