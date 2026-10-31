from django.core.management.base import BaseCommand, CommandError

from apps.demo import integrity


class Command(BaseCommand):
    help = "Check that invoices, lines, payments, discounts and subscriptions are consistent."

    def handle(self, *args, **opts):
        problems = integrity.run_checks()
        for p in problems:
            self.stderr.write(self.style.ERROR(p))
        if problems:
            raise CommandError(f"{len(problems)} problem(s) found.")
        self.stdout.write(self.style.SUCCESS("All checks passed."))
