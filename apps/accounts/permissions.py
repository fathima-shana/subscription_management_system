from apps.core.permissions import role_required

from .models import Role

admin_required = role_required(Role.ADMIN)
internal_required = role_required(Role.ADMIN, Role.STAFF)
customer_required = role_required(Role.CUSTOMER)
