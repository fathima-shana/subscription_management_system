# Entity relationship diagram

Rendered automatically by GitHub and by the VS Code "Markdown Preview Mermaid Support" extension.

```mermaid
erDiagram
    USER ||--o| CUSTOMER_PROFILE : "has (customers)"
    USER ||--o{ AUDIT_LOG : "acts in"

    TAX ||--o{ PRODUCT : "default tax"
    PRODUCT ||--o{ PRODUCT_VARIANT : "has options"
    PRODUCT }o--o{ DISCOUNT : "targeted by"

    PLAN ||--o{ QUOTATION_TEMPLATE : "used by"
    QUOTATION_TEMPLATE ||--o{ TEMPLATE_LINE : "contains"
    PRODUCT ||--o{ TEMPLATE_LINE : "in"

    CUSTOMER_PROFILE ||--o{ SUBSCRIPTION : "owns"
    PLAN ||--o{ SUBSCRIPTION : "billed by"
    PAYMENT_TERM ||--o{ SUBSCRIPTION : "due in"
    QUOTATION_TEMPLATE |o--o{ SUBSCRIPTION : "started from"
    SUBSCRIPTION |o--o{ SUBSCRIPTION : "renewed as"
    SUBSCRIPTION ||--o{ SUBSCRIPTION_LINE : "has"
    PRODUCT ||--o{ SUBSCRIPTION_LINE : "sold as"
    TAX |o--o{ SUBSCRIPTION_LINE : "taxed with"
    SUBSCRIPTION ||--o{ STATUS_HISTORY : "records"

    SUBSCRIPTION ||--o{ INVOICE : "billed as"
    CUSTOMER_PROFILE ||--o{ INVOICE : "receives"
    DISCOUNT |o--o{ INVOICE : "applied to"
    INVOICE ||--o{ INVOICE_LINE : "has"
    INVOICE ||--o{ PAYMENT : "settled by"

    USER {
        bigint id PK
        string email UK "unique, case-insensitive"
        string role "admin | staff | customer"
        bool is_active
    }
    CUSTOMER_PROFILE {
        bigint id PK
        bigint user_id FK "one to one"
        string company_name
        text billing_address
        string tax_id
    }
    TAX {
        bigint id PK
        string name UK
        string tax_type "percentage | fixed"
        decimal rate
        bool is_active
    }
    PRODUCT {
        bigint id PK
        string name UK
        string product_type
        decimal sales_price
        decimal cost_price
        bigint default_tax_id FK
    }
    PRODUCT_VARIANT {
        bigint id PK
        bigint product_id FK
        string attribute
        string value
        decimal extra_price
    }
    PLAN {
        bigint id PK
        string name UK
        string billing_period "daily | weekly | monthly | yearly"
        int min_quantity
        date start_date
        date end_date
        bool auto_close
        bool closable
        bool pausable
        bool renewable
    }
    DISCOUNT {
        bigint id PK
        string name UK
        string discount_type "percentage | fixed"
        decimal value
        string applies_to "product | subscription"
        decimal min_purchase
        int min_quantity
        date start_date
        date end_date
        int usage_limit
        int used_count "never above usage_limit (CHECK)"
    }
    PAYMENT_TERM {
        bigint id PK
        string name UK
        int days
    }
    QUOTATION_TEMPLATE {
        bigint id PK
        string name UK
        bigint plan_id FK
        int validity_days
    }
    TEMPLATE_LINE {
        bigint id PK
        bigint template_id FK
        bigint product_id FK
        bigint variant_id FK
        int quantity
    }
    SUBSCRIPTION {
        bigint id PK
        string number UK "SUB-00001"
        bigint customer_id FK
        bigint plan_id FK
        bigint payment_term_id FK
        string status "draft..closed"
        date start_date
        date expiry_date
        date next_billing_date "partial index while active"
        date quote_valid_until
    }
    SUBSCRIPTION_LINE {
        bigint id PK
        bigint subscription_id FK
        bigint product_id FK
        bigint variant_id FK
        int quantity
        decimal unit_price "snapshot"
        bigint tax_id FK
    }
    STATUS_HISTORY {
        bigint id PK
        bigint subscription_id FK
        string from_status
        string to_status
        bigint changed_by_id FK
        datetime changed_at
    }
    INVOICE {
        bigint id PK
        string number UK "INV-00001"
        bigint subscription_id FK
        bigint customer_id FK
        string status "draft | confirmed | paid | cancelled"
        date period_start "UNIQUE with subscription_id"
        date period_end
        date issue_date
        date due_date
        decimal subtotal
        decimal discount_total
        decimal tax_total
        decimal total
        decimal amount_paid "sum of live payments"
        string bill_to_name "snapshot"
        string plan_name "snapshot"
    }
    INVOICE_LINE {
        bigint id PK
        bigint invoice_id FK
        string description "snapshot"
        int quantity
        decimal unit_price "snapshot"
        decimal discount_amount
        string tax_name "snapshot"
        decimal tax_rate "snapshot"
        decimal total
    }
    PAYMENT {
        bigint id PK
        bigint invoice_id FK
        string method "cash | bank | card | upi"
        decimal amount "greater than 0 (CHECK)"
        date payment_date
        datetime voided_at "never deleted"
    }
    AUDIT_LOG {
        bigint id PK
        bigint actor_id FK
        string action
        string entity_type
        string entity_id
        json details "before / after"
    }
    NUMBER_SEQUENCE {
        string key UK "subscription | invoice"
        int next_value "row-locked counter"
    }
```

## Constraints worth knowing (all enforced by PostgreSQL, not just by forms)

| Rule | Where |
|---|---|
| A subscription period is never billed twice | `UNIQUE (subscription_id, period_start)` on invoices |
| Names are unique ignoring case (user email, product, plan, tax, discount, template) | `UNIQUE (lower(name))` |
| Discount usage never exceeds its limit | `CHECK used_count <= usage_limit` plus one guarded `UPDATE` |
| An invoice is never over-paid | `CHECK amount_paid <= total` |
| Payments are positive | `CHECK amount > 0` |
| Dates and quantities make sense | `CHECK end_date >= start_date`, `quantity >= 1`, percent `<= 100` |
| "Due for billing" lookups stay fast | partial index on `next_billing_date` where `status = 'active'` |
| Overdue lookups stay fast | index on `(due_date, status)` |

## Why invoices copy data instead of pointing at it

Invoice and invoice-line rows store the customer name, address, plan name, product description, unit price,
tax name and rate, and discount. Changing a price, a tax rate or a customer later never alters an invoice that was
already issued. Overdue is derived from `due_date`, `status` and the balance, so it can never go stale.
