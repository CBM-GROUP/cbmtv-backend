"""The public Content boundary shared by catalogue and child endpoints."""

from rest_framework.exceptions import PermissionDenied, ValidationError


PUBLIC_STATUS = 'approved'


def dashboard_scope(request):
    """An explicit staff-only read scope; signing in alone never expands public reads."""
    scope = request.query_params.get('scope')
    if scope is None:
        return False
    if scope != 'dashboard':
        raise ValidationError({'scope': 'Use scope=dashboard for staff reads.'})
    if not request.user.is_authenticated or not request.user.is_staff:
        raise PermissionDenied('Dashboard Content access requires a staff account.')
    return True


def visible_queryset(request, queryset, status_field='status'):
    if request.method not in ('GET', 'HEAD', 'OPTIONS') or dashboard_scope(request):
        return queryset
    return queryset.filter(**{status_field: PUBLIC_STATUS})
