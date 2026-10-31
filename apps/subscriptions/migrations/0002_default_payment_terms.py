from django.db import migrations

DEFAULTS = [("Immediate payment", 0), ("15 days", 15), ("30 days", 30), ("45 days", 45)]


def add_terms(apps, schema_editor):
    PaymentTerm = apps.get_model("subscriptions", "PaymentTerm")
    for name, days in DEFAULTS:
        PaymentTerm.objects.get_or_create(name=name, defaults={"days": days})


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0001_initial")]

    operations = [migrations.RunPython(add_terms, migrations.RunPython.noop)]
