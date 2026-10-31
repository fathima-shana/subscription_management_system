import time

from django.core.management.base import BaseCommand, CommandError

from apps.demo import integrity, seed


class Command(BaseCommand):
    help = (
        "Load demo data: customers, subscriptions, invoices and payments, in bulk. "
        "Default is 10,000 subscriptions (about 30,000 invoices). Demo logins use the @demo.example domain."
    )

    def add_arguments(self, parser):
        parser.add_argument("--subscriptions", type=int, default=10000)
        parser.add_argument(
            "--customers", type=int, default=None, help="Default: a quarter of the subscriptions."
        )
        parser.add_argument(
            "--seed", type=int, default=42, help="Random seed; the same seed gives the same data."
        )
        parser.add_argument(
            "--reset-demo",
            action="store_true",
            help="First delete previously loaded demo customers and their data.",
        )
        parser.add_argument(
            "--no-verify", action="store_true", help="Skip the integrity check at the end."
        )

    def handle(self, *args, **opts):
        from apps.accounts.models import CustomerProfile
        from apps.subscriptions.models import Subscription

        if opts["subscriptions"] < 1:
            raise CommandError("--subscriptions must be at least 1")
        demo_exists = CustomerProfile.objects.filter(
            user__email__endswith=f"@{seed.DEMO_DOMAIN}", user__role="customer"
        ).exists()
        if demo_exists and not opts["reset_demo"]:
            raise CommandError("Demo data is already loaded. Add --reset-demo to replace it.")
        if opts["reset_demo"]:
            self.stdout.write("Removing the previous demo data...")
            seed.reset_demo()
        started = time.time()
        totals = seed.seed(
            opts["subscriptions"], opts["customers"], opts["seed"], stdout=self.stdout
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {totals['customers']} customers, {totals['subscriptions']} subscriptions, {totals['invoices']} invoices, "
                f"{totals['payments']} payments in {time.time() - started:.0f}s."
            )
        )
        if not opts["no_verify"]:
            problems = integrity.run_checks()
            if problems:
                for p in problems:
                    self.stderr.write(self.style.ERROR(p))
                raise CommandError("Integrity check failed.")
            self.stdout.write(
                self.style.SUCCESS(
                    "Integrity check passed: every invoice, line and payment adds up."
                )
            )
        self.stdout.write(
            f"\nSign in at /accounts/login/ with:\n  admin     admin@{seed.DEMO_DOMAIN}\n  staff     staff@{seed.DEMO_DOMAIN}\n"
            f"  customer  customer00001@{seed.DEMO_DOMAIN}\nPassword for all: {seed.DEMO_PASSWORD}"
        )
