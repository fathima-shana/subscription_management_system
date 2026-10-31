"""Demo data generator: realistic customers, subscriptions, invoices and payments, in bulk.

Records are built in memory and inserted with bulk_create in batches (one transaction per batch),
so 10,000 subscriptions and about 30,000 invoices load in a minute or two instead of an hour.
It does NOT call the billing engine for every invoice; the numbers are produced with the same
pricing function (apps/subscriptions/pricing.py), and `integrity.run_checks()` proves the books
add up afterwards. A fixed random seed makes every run produce the same data.
"""

import datetime
import random
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.hashers import make_password
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import CustomerProfile, Role, User
from apps.billing.models import Invoice, InvoiceLine, InvoiceStatus
from apps.billing.periods import next_period_start, period_end
from apps.catalog.models import Plan, Product, ProductVariant
from apps.core import audit
from apps.core.models import NumberSequence
from apps.core.money import quantize
from apps.discounts.models import Discount
from apps.payments.models import Payment
from apps.subscriptions.models import (
    PaymentTerm,
    QuotationTemplate,
    QuotationTemplateLine,
    Status,
    Subscription,
    SubscriptionLine,
    SubscriptionStatusHistory,
)
from apps.subscriptions.pricing import price_lines
from apps.taxes.models import Tax

DEMO_DOMAIN = "demo.example"
DEMO_PASSWORD = "Demo!Pass123"

FIRST = [
    "Aarav",
    "Anika",
    "Arjun",
    "Diya",
    "Farhan",
    "Gita",
    "Hari",
    "Isha",
    "Jacob",
    "Kavya",
    "Liam",
    "Meera",
    "Nikhil",
    "Olivia",
    "Priya",
    "Rahul",
    "Sara",
    "Tara",
    "Uday",
    "Vikram",
    "Zoya",
    "Noah",
    "Emma",
    "Ravi",
    "Sneha",
    "Omar",
    "Lakshmi",
    "Mohan",
    "Anita",
    "Devika",
]
LAST = [
    "Nair",
    "Menon",
    "Sharma",
    "Iyer",
    "Khan",
    "Patel",
    "Thomas",
    "George",
    "Varma",
    "Reddy",
    "Das",
    "Singh",
    "Pillai",
    "Joseph",
    "Mathew",
    "Kumar",
    "Rao",
    "Bose",
    "Hassan",
    "Fernandez",
]
COMPANY_A = [
    "Bright",
    "Blue",
    "Kerala",
    "Urban",
    "Prime",
    "Nova",
    "Coastal",
    "Zen",
    "Metro",
    "Apex",
    "Green",
    "Swift",
    "Lotus",
    "Horizon",
    "Vertex",
    "Orbit",
    "Cedar",
    "Pioneer",
    "Harbour",
    "Summit",
]
COMPANY_B = [
    "Traders",
    "Systems",
    "Foods",
    "Logistics",
    "Textiles",
    "Labs",
    "Retail",
    "Studios",
    "Clinics",
    "Motors",
    "Estates",
    "Learning",
    "Energy",
    "Media",
    "Hospitality",
]
SUFFIX = ["Pvt Ltd", "LLP", "& Co", "Industries", "Enterprises", "Ltd"]
STREETS = [
    "MG Road",
    "Marine Drive",
    "Park Street",
    "Beach Road",
    "Station Road",
    "Church Street",
    "Temple Road",
]
CITIES = [
    "Kozhikode",
    "Kochi",
    "Thiruvananthapuram",
    "Bengaluru",
    "Chennai",
    "Mumbai",
    "Dubai",
    "Doha",
]

# (name, type, price, cost, tax name, [(attribute, value, extra), ...])
PRODUCTS = [
    (
        "CRM Suite",
        "service",
        "1000",
        "400",
        "GST 18",
        [("Brand", "Standard", "0"), ("Brand", "Odoo", "560"), ("Brand", "Premium", "1200")],
    ),
    (
        "Helpdesk",
        "service",
        "500",
        "150",
        "GST 18",
        [("Tier", "Basic", "0"), ("Tier", "Plus", "250")],
    ),
    ("Billing Engine", "service", "1500", "600", "GST 18", []),
    (
        "POS Cloud",
        "service",
        "800",
        "300",
        "GST 12",
        [("Terminals", "1", "0"), ("Terminals", "5", "1500")],
    ),
    ("Inventory Pro", "service", "1200", "500", "GST 12", []),
    ("Analytics Add-on", "service", "300", "80", "GST 18", []),
    ("Gold Support", "service", "2500", "1200", "GST 18", []),
    ("Training Pack", "service", "900", "300", "GST 5", []),
    ("Barcode Scanner", "goods", "3500", "2400", "GST 18", []),
    (
        "API Access",
        "service",
        "400",
        "100",
        "GST 18",
        [("Calls", "100k", "0"), ("Calls", "1M", "900")],
    ),
    ("Backup Storage 1TB", "service", "250", "90", "GST 18", []),
    ("Email Hosting", "service", "150", "40", "GST 18", []),
]
TAXES = [("GST 5", "5"), ("GST 12", "12"), ("GST 18", "18")]
PLANS = [  # name, period, weight, kwargs
    ("Monthly Basic", "monthly", 40, {"pausable": True}),
    ("Monthly Plus", "monthly", 30, {"pausable": True, "min_quantity": 1}),
    ("Yearly Pro", "yearly", 10, {"auto_close": False}),
    ("Weekly Trial", "weekly", 12, {"pausable": False, "renewable": False}),
    ("Daily Pass", "daily", 8, {"pausable": False, "closable": True}),
]
MAX_DAYS_BACK = {"monthly": 260, "yearly": 1000, "weekly": 50, "daily": 8}
STATUS_WEIGHTS = [
    (Status.ACTIVE, 60),
    (Status.CLOSED, 15),
    (Status.PAUSED, 5),
    (Status.CONFIRMED, 5),
    (Status.QUOTATION, 5),
    (Status.DRAFT, 10),
]
HISTORY_PATH = {
    Status.DRAFT: [],
    Status.QUOTATION: ["quotation"],
    Status.CONFIRMED: ["quotation", "confirmed"],
    Status.ACTIVE: ["quotation", "confirmed", "active"],
    Status.PAUSED: ["quotation", "confirmed", "active", "paused"],
    Status.CLOSED: ["quotation", "confirmed", "active", "closed"],
}
METHODS = [("upi", 40), ("bank", 25), ("card", 20), ("cash", 15)]
BATCH = 1000


def _pick(rng, weighted):
    return rng.choices([w[0] for w in weighted], weights=[w[1] for w in weighted])[0]


def _money(value):
    return quantize(Decimal(value))


def ensure_catalog(stdout=None):
    """Create the demo catalog if it is missing (never touches existing rows)."""
    taxes = {
        n: Tax.objects.get_or_create(
            name=n, defaults={"tax_type": "percentage", "rate": Decimal(r)}
        )[0]
        for n, r in TAXES
    }
    products, variants = {}, {}
    for name, ptype, price, cost, tax_name, opts in PRODUCTS:
        p, _ = Product.objects.get_or_create(
            name=name,
            defaults={
                "product_type": ptype,
                "sales_price": _money(price),
                "cost_price": _money(cost),
                "default_tax": taxes[tax_name],
            },
        )
        products[name] = p
        for attr, value, extra in opts:
            v, _ = ProductVariant.objects.get_or_create(
                product=p, attribute=attr, value=value, defaults={"extra_price": _money(extra)}
            )
            variants.setdefault(name, []).append(v)
    plans = {}
    for name, period, _w, extra in PLANS:
        plans[name], _ = Plan.objects.get_or_create(
            name=name, defaults={"billing_period": period, **extra}
        )
    discount_specs = [
        (
            "Welcome 10%",
            {
                "discount_type": "percentage",
                "value": Decimal("10"),
                "min_purchase": Decimal("2000"),
            },
            [],
        ),
        (
            "Bulk seats Rs 500 off",
            {"discount_type": "fixed", "value": Decimal("500"), "min_quantity": 5},
            [],
        ),
        (
            "CRM Loyalty 15%",
            {"discount_type": "percentage", "value": Decimal("15"), "applies_to": "product"},
            ["CRM Suite"],
        ),
        (
            "First 50 customers 20%",
            {"discount_type": "percentage", "value": Decimal("20"), "usage_limit": 50},
            [],
        ),
    ]
    for name, fields, targets in discount_specs:
        d, created = Discount.objects.get_or_create(name=name, defaults=fields)
        if created and targets:
            d.products.set([products[t] for t in targets])
    for name, plan_name, lines in [
        ("Starter pack", "Monthly Basic", [("CRM Suite", 1), ("Helpdesk", 2)]),
        (
            "Retail bundle",
            "Monthly Plus",
            [("POS Cloud", 1), ("Inventory Pro", 1), ("Barcode Scanner", 2)],
        ),
        (
            "Annual enterprise",
            "Yearly Pro",
            [("Billing Engine", 5), ("Gold Support", 1), ("Analytics Add-on", 5)],
        ),
    ]:
        t, created = QuotationTemplate.objects.get_or_create(
            name=name, defaults={"plan": plans[plan_name], "validity_days": 30}
        )
        if created:
            for product_name, qty in lines:
                QuotationTemplateLine.objects.create(
                    template=t, product=products[product_name], quantity=qty
                )
    return SimpleNamespace(taxes=taxes, products=products, variants=variants, plans=plans)


def ensure_staff():
    users = {}
    for email, name, role, extra in [
        (
            f"admin@{DEMO_DOMAIN}",
            "Demo Admin",
            Role.ADMIN,
            {"is_staff": True, "is_superuser": True},
        ),
        (f"staff@{DEMO_DOMAIN}", "Demo Staff", Role.STAFF, {}),
    ]:
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(
                email, DEMO_PASSWORD, full_name=name, role=role, **extra
            )
        users[role] = user
    return users


def reset_demo():
    """Delete only demo customers and everything that belongs to them (never real data)."""
    customers = CustomerProfile.objects.filter(
        user__email__endswith=f"@{DEMO_DOMAIN}", user__role=Role.CUSTOMER
    )
    subs = Subscription.objects.filter(customer__in=customers)
    invoices = Invoice.objects.filter(subscription__in=subs)
    Payment.objects.filter(invoice__in=invoices).delete()
    InvoiceLine.objects.filter(invoice__in=invoices).delete()
    invoices.delete()
    SubscriptionLine.objects.filter(subscription__in=subs).delete()
    SubscriptionStatusHistory.objects.filter(subscription__in=subs).delete()
    Subscription.objects.filter(renewed_from__in=subs).update(renewed_from=None)
    subs.delete()
    User.objects.filter(pk__in=customers.values("user_id")).delete()
    Discount.objects.update(used_count=0)
    return True


def _create_customers(rng, count):
    start = (
        User.objects.filter(email__startswith="customer", email__endswith=f"@{DEMO_DOMAIN}").count()
    ) + 1
    hashed = make_password(DEMO_PASSWORD)
    profiles = {}
    for lo in range(0, count, BATCH):
        n = min(BATCH, count - lo)
        users = []
        for i in range(n):
            idx = start + lo + i
            first, last = rng.choice(FIRST), rng.choice(LAST)
            users.append(
                User(
                    email=f"customer{idx:05d}@{DEMO_DOMAIN}",
                    full_name=f"{first} {last}",
                    role=Role.CUSTOMER,
                    password=hashed,
                )
            )
        with transaction.atomic():
            User.objects.bulk_create(users)
            created = list(User.objects.filter(email__in=[u.email for u in users]))
            by_email = {u.email: u for u in created}
            rows = []
            for u in users:
                company = f"{rng.choice(COMPANY_A)} {rng.choice(COMPANY_B)} {rng.choice(SUFFIX)}"
                rows.append(
                    CustomerProfile(
                        user=by_email[u.email],
                        company_name=company,
                        phone=f"+91 9{rng.randint(100000000, 999999999)}",
                        billing_address=f"{rng.randint(1, 250)} {rng.choice(STREETS)}\n{rng.choice(CITIES)}",
                        tax_id=f"32{''.join(rng.choices('ABCDEFGHJKLMNPQRSTUVWXYZ', k=5))}{rng.randint(1000, 9999)}F1Z{rng.randint(1, 9)}",
                    )
                )
            CustomerProfile.objects.bulk_create(rows)
    qs = CustomerProfile.objects.filter(
        user__email__startswith="customer", user__email__endswith=f"@{DEMO_DOMAIN}"
    )
    for p in qs.select_related("user").order_by("-id")[:count]:
        profiles[p.pk] = (
            p.company_name or p.user.full_name,
            p.user.email,
            p.billing_address,
            p.tax_id,
        )
    return profiles


def _line_specs(rng, catalog):
    """1-4 distinct products, sometimes a variant, quantity 1-10."""
    names = rng.sample(list(catalog.products), rng.randint(1, 4))
    specs = []
    for name in names:
        product = catalog.products[name]
        variant = (
            rng.choice(catalog.variants[name])
            if name in catalog.variants and rng.random() < 0.6
            else None
        )
        qty = rng.choices([1, 2, 3, 5, 10], weights=[40, 25, 15, 12, 8])[0]
        specs.append((product, variant, qty))
    return specs


def _periods(start, billing_period, today):
    out, current = [], start
    while current <= today and len(out) < 150:
        out.append(current)
        current = next_period_start(current, billing_period, start)
    return out, current  # current = first period start after today


def seed(subscriptions=10000, customers=None, seed_value=42, stdout=None, stderr=None):
    """Load demo data. Returns a dict of counts."""
    rng = random.Random(seed_value)
    today = timezone.localdate()
    say = (lambda m: stdout.write(m)) if stdout else (lambda m: None)
    customers = customers or max(1, subscriptions // 4)

    say("Preparing catalog and demo users...")
    catalog = ensure_catalog()
    people = ensure_staff()
    staff_user = people[Role.STAFF]
    terms = list(PaymentTerm.objects.all())
    candidates = list(
        Discount.objects.filter(
            is_active=True, usage_limit__isnull=True, start_date__isnull=True, end_date__isnull=True
        ).prefetch_related("products")
    )
    plan_choices = [(catalog.plans[n], w) for n, _p, w, _e in PLANS]

    say(f"Creating {customers} customers...")
    profiles = _create_customers(rng, customers)
    profile_ids = list(profiles)

    sub_seq = NumberSequence.objects.filter(key="subscription").first()
    inv_seq = NumberSequence.objects.filter(key="invoice").first()
    next_sub = sub_seq.next_value if sub_seq else 1
    next_inv = inv_seq.next_value if inv_seq else 1
    discount_use, totals = {}, {"subscriptions": 0, "invoices": 0, "payments": 0, "lines": 0}

    for lo in range(0, subscriptions, BATCH):
        n = min(BATCH, subscriptions - lo)
        say(f"  subscriptions {lo + 1}-{lo + n} of {subscriptions}")
        plans_info, subs = [], []
        for _ in range(n):
            plan = _pick(rng, plan_choices)
            status = _pick(rng, STATUS_WEIGHTS)
            term = rng.choice(terms)
            pid = rng.choice(profile_ids)
            lines = _line_specs(rng, catalog)
            info = {
                "plan": plan,
                "status": status,
                "term": term,
                "pid": pid,
                "lines": lines,
                "invoices": [],
                "next": None,
                "expiry": None,
                "quote_until": None,
            }
            if status in (Status.ACTIVE, Status.PAUSED, Status.CLOSED):
                start = today - datetime.timedelta(
                    days=rng.randint(0, MAX_DAYS_BACK[plan.billing_period])
                )
                periods, after = _periods(start, plan.billing_period, today)
                if status == Status.CLOSED:
                    keep = rng.randint(1, len(periods))
                    info["invoices"] = periods[:keep]
                    info["expiry"] = period_end(periods[keep - 1], plan.billing_period, start)
                else:
                    due_now = status == Status.ACTIVE and rng.random() < 0.03
                    info["invoices"] = periods[:-1] if due_now else periods
                    info["next"] = periods[-1] if due_now else after
                    if status == Status.ACTIVE and not due_now and rng.random() < 0.15:
                        info["expiry"] = period_end(after, plan.billing_period, start)
            else:
                start = today + datetime.timedelta(days=rng.randint(-10, 30))
                if status in (Status.DRAFT, Status.QUOTATION):
                    info["quote_until"] = today + datetime.timedelta(days=rng.randint(-5, 30))
            info["start"] = start
            plans_info.append(info)
            subs.append(
                Subscription(
                    number=f"SUB-{next_sub + len(subs):05d}",
                    customer_id=info["pid"],
                    plan=plan,
                    payment_term=term,
                    status=status.value,
                    start_date=start,
                    expiry_date=info["expiry"],
                    quote_valid_until=info["quote_until"],
                    next_billing_date=info["next"],
                    notes="",
                    created_by=staff_user,
                )
            )
        next_sub += n

        with transaction.atomic():
            Subscription.objects.bulk_create(subs)
            sub_lines, history, invoices, inv_meta = [], [], [], []
            for sub, info in zip(subs, plans_info):
                history.append(
                    SubscriptionStatusHistory(
                        subscription=sub,
                        from_status="",
                        to_status="draft",
                        changed_by=staff_user,
                        note="Demo data",
                    )
                )
                prev = "draft"
                for step in HISTORY_PATH[info["status"]]:
                    history.append(
                        SubscriptionStatusHistory(
                            subscription=sub,
                            from_status=prev,
                            to_status=step,
                            changed_by=staff_user,
                        )
                    )
                    prev = step
                fake_lines = []
                for product, variant, qty in info["lines"]:
                    price = product.price_for(variant)
                    sub_lines.append(
                        SubscriptionLine(
                            subscription=sub,
                            product=product,
                            variant=variant,
                            quantity=qty,
                            unit_price=price,
                            tax=product.default_tax,
                        )
                    )
                    fake_lines.append(
                        SimpleNamespace(
                            product_id=product.pk,
                            quantity=qty,
                            unit_price=price,
                            tax=product.default_tax,
                            label=(
                                product.name
                                if variant is None
                                else f"{product.name} ({variant.label})"
                            ),
                            product=product,
                        )
                    )
                if not info["invoices"]:
                    continue
                priced = price_lines(fake_lines, candidates, today)
                profile = profiles[info["pid"]]
                for start_day in info["invoices"]:
                    due = start_day + datetime.timedelta(days=info["term"].days)
                    status = InvoiceStatus.CONFIRMED
                    paid, pay_date = Decimal("0.00"), None
                    roll = rng.random()
                    if roll < 0.01:
                        status = InvoiceStatus.CANCELLED
                    elif due < today:
                        if roll < 0.83:
                            paid, pay_date = priced.total, start_day + datetime.timedelta(
                                days=rng.randint(
                                    0, max(0, min((today - start_day).days, info["term"].days + 12))
                                )
                            )
                        elif roll < 0.91:
                            paid = quantize(priced.total * Decimal(rng.randint(20, 80)) / 100)
                            pay_date = start_day + datetime.timedelta(
                                days=rng.randint(0, max(0, (today - start_day).days))
                            )
                    elif roll < 0.36:
                        paid, pay_date = priced.total, start_day + datetime.timedelta(
                            days=rng.randint(0, max(0, (today - start_day).days))
                        )
                    if paid >= priced.total and status != InvoiceStatus.CANCELLED:
                        status = InvoiceStatus.PAID
                    inv = Invoice(
                        number=f"INV-{next_inv + len(invoices):05d}",
                        subscription=sub,
                        customer_id=info["pid"],
                        status=status,
                        period_start=start_day,
                        period_end=period_end(
                            start_day, info["plan"].billing_period, info["start"]
                        ),
                        issue_date=start_day,
                        due_date=due,
                        bill_to_name=profile[0],
                        bill_to_email=profile[1],
                        bill_to_address=profile[2],
                        bill_to_tax_id=profile[3],
                        plan_name=info["plan"].name,
                        payment_term_name=info["term"].name,
                        discount=priced.applied.discount if priced.applied else None,
                        discount_name=priced.applied.discount.name if priced.applied else "",
                        subtotal=priced.subtotal,
                        discount_total=priced.discount_total,
                        tax_total=priced.tax_total,
                        total=priced.total,
                        amount_paid=paid,
                    )
                    invoices.append(inv)
                    inv_meta.append((priced, paid, pay_date))
                    if priced.applied:
                        discount_use[priced.applied.discount.pk] = (
                            discount_use.get(priced.applied.discount.pk, 0) + 1
                        )
            SubscriptionLine.objects.bulk_create(sub_lines)
            SubscriptionStatusHistory.objects.bulk_create(history)
            Invoice.objects.bulk_create(invoices)
            rows, pays = [], []
            for inv, (priced, paid, pay_date) in zip(invoices, inv_meta):
                for p in priced.lines:
                    tax = p.line.tax
                    rows.append(
                        InvoiceLine(
                            invoice=inv,
                            product=p.line.product,
                            description=p.line.label,
                            quantity=p.line.quantity,
                            unit_price=p.line.unit_price,
                            amount=p.amount,
                            discount_amount=p.discount,
                            tax_name=tax.name if tax else "",
                            tax_type=tax.tax_type if tax else "",
                            tax_rate=tax.rate if tax else 0,
                            tax_amount=p.tax,
                            total=p.total,
                        )
                    )
                if paid > 0:
                    pays.append(
                        Payment(
                            invoice=inv,
                            method=_pick(rng, METHODS),
                            amount=paid,
                            payment_date=pay_date,
                            note=f"REF{rng.randint(100000, 999999)}",
                            created_by=staff_user,
                        )
                    )
            InvoiceLine.objects.bulk_create(rows)
            Payment.objects.bulk_create(pays)
        next_inv += len(invoices)
        totals["subscriptions"] += len(subs)
        totals["invoices"] += len(invoices)
        totals["payments"] += len(pays)
        totals["lines"] += len(rows)

    with transaction.atomic():
        NumberSequence.objects.update_or_create(
            key="subscription", defaults={"next_value": next_sub}
        )
        NumberSequence.objects.update_or_create(key="invoice", defaults={"next_value": next_inv})
        for pk, n in discount_use.items():
            Discount.objects.filter(pk=pk).update(used_count=F("used_count") + n)
        audit.record(
            actor=people[Role.ADMIN], action="demo.seeded", details={**totals, "seed": seed_value}
        )
    with connection.cursor() as cursor:  # fresh statistics, so the planner picks good plans
        cursor.execute("ANALYZE")
    totals["customers"] = len(profiles)
    return totals
