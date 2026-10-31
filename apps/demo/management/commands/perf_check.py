from django.core.management.base import BaseCommand, CommandError

from apps.demo import perf


class Command(BaseCommand):
    help = "Time the main pages on the current database and flag any slower than the limit (default 2000 ms)."

    def add_arguments(self, parser):
        parser.add_argument("--limit-ms", type=int, default=2000)
        parser.add_argument(
            "--runs",
            type=int,
            default=3,
            help="Each page is loaded this many times; the median is reported.",
        )
        parser.add_argument(
            "--explain", action="store_true", help="Also print EXPLAIN ANALYZE for the key queries."
        )
        parser.add_argument(
            "--billing",
            action="store_true",
            help="Also time a billing run (rolled back, nothing is saved).",
        )
        parser.add_argument(
            "--markdown",
            action="store_true",
            help="Print the table as Markdown (paste into docs/PERFORMANCE.md).",
        )

    def handle(self, *args, **opts):
        limit = opts["limit_ms"]
        try:
            results = perf.run(runs=opts["runs"])
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        from apps.billing.models import Invoice
        from apps.subscriptions.models import Subscription

        self.stdout.write(
            f"Database: {Subscription.objects.count()} subscriptions, {Invoice.objects.count()} invoices\n"
        )
        if opts["markdown"]:
            self.stdout.write("| Page | Time (ms) | Queries | Size (KB) |\n|---|---:|---:|---:|")
        slow, bad = [], []
        for r in results:
            if r.status != 200:
                bad.append(r)
            if r.ms > limit:
                slow.append(r)
            flag = " SLOW" if r.ms > limit else ""
            if opts["markdown"]:
                self.stdout.write(f"| {r.name} | {r.ms:.0f} | {r.queries} | {r.kb:.0f} |")
            else:
                self.stdout.write(
                    f"{r.ms:8.0f} ms  {r.queries:3d} queries  {r.kb:7.0f} KB  HTTP {r.status}  {r.name}{flag}"
                )
        if opts["billing"]:
            b = perf.billing_throughput()
            per_sec = b["billed"] / b["seconds"] if b["seconds"] else 0
            self.stdout.write(
                f"\nBilling run (rolled back): {b['billed']} invoices from {b['due']} due in {b['seconds']:.1f}s "
                f"({per_sec:.0f} invoices/second), {b['failed']} failed"
            )
        if opts["explain"]:
            for title, qs in perf.explain_queries():
                self.stdout.write(f"\n--- EXPLAIN ANALYZE: {title}")
                self.stdout.write(qs.explain(analyze=True))
        self.stdout.write("")
        if bad:
            raise CommandError(
                f"{len(bad)} page(s) did not return HTTP 200: " + ", ".join(r.name for r in bad)
            )
        if slow:
            raise CommandError(
                f"{len(slow)} page(s) slower than {limit} ms: " + ", ".join(r.name for r in slow)
            )
        self.stdout.write(self.style.SUCCESS(f"All {len(results)} pages are under {limit} ms."))
