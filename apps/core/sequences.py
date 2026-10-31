"""Document numbers (SUB-00001, INV-00001, ...).

The counter row is locked with SELECT ... FOR UPDATE, so two requests can never receive the
same number. Because the number is taken inside the caller's transaction, a rollback also
rolls the counter back and no number is skipped.
"""

from django.db import IntegrityError, transaction

from .models import NumberSequence


def next_number(key: str, prefix: str, width: int = 5) -> str:
    try:
        with transaction.atomic():
            NumberSequence.objects.get_or_create(key=key)
    except IntegrityError:  # another request created it first; fine
        pass
    seq = NumberSequence.objects.select_for_update().get(key=key)
    value = seq.next_value
    seq.next_value = value + 1
    seq.save(update_fields=["next_value"])
    return f"{prefix}-{value:0{width}d}"
