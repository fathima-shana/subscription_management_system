import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.billing import engine


class Command(BaseCommand):
    help = "Create invoices for every subscription that is due. Safe to run repeatedly (for example from cron)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Bill as if today were this date (YYYY-MM-DD).")
        parser.add_argument(
            "--dry-run", action="store_true", help="Only list what is due; change nothing."
        )

    def handle(self, *args, **options):
        today = None
        if options["date"]:
            try:
                today = datetime.date.fromisoformat(options["date"])
            except ValueError as exc:
                raise CommandError("--date must look like 2026-12-31") from exc
        if options["dry_run"]:
            from django.utils import timezone

            today = today or timezone.localdate()
            due = engine.due_subscriptions(today).order_by("next_billing_date", "id")
            for sub in due:
                self.stdout.write(f"{sub.number}  due {sub.next_billing_date}")
            self.stdout.write(
                self.style.SUCCESS(
                    f"{due.count()} subscription(s) due on {today}. Nothing was changed."
                )
            )
            return
        result = engine.run(today=today)
        for _, number, sub_number, total in result.created:
            self.stdout.write(f"{number}  {sub_number}  Rs {total}")
        for sub_number, message in result.failed:
            self.stderr.write(self.style.ERROR(f"FAILED {sub_number}: {message}"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Billing for {result.today}: {len(result.created)} invoice(s), Rs {result.total_billed}, "
                f"{len(result.closed)} closed, {len(result.ended)} stopped, {len(result.failed)} failed."
            )
        )
