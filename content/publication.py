"""The public Content boundary shared by catalogue and child endpoints."""

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission


PUBLIC_STATUS = 'approved'


def dashboard_sections(user):
    if not user.is_authenticated or not user.is_active or getattr(user, 'must_change_password', False):
        return ()

    # The released User model has no dashboard_role field. Preserve its staff
    # access until the separate role migration is integrated.
    if not hasattr(user, 'dashboard_role_id'):
        return ('channels', 'content', 'adverts') if user.is_staff else ()

    # Match the section permissions in the pending dashboard-role contract.
    if user.is_superuser or (user.is_staff and user.role in ('admin', 'internal_admin')):
        return ('channels', 'content', 'adverts', 'settings', 'users')
    role = getattr(user, 'dashboard_role', None)
    return tuple(role.permissions) if role else ()


def can_read_dashboard_content(user):
    return 'content' in dashboard_sections(user)


class HasDashboardMediaAccess(BasePermission):
    def has_permission(self, request, view):
        return any(section in dashboard_sections(request.user) for section in ('channels', 'content', 'adverts'))


def dashboard_scope(request):
    """An explicit Content-editor read scope; signing in alone changes nothing."""
    scope = request.query_params.get('scope')
    if scope is None:
        return False
    if scope != 'dashboard':
        raise ValidationError({'scope': 'Use scope=dashboard for authorized dashboard reads.'})
    if not can_read_dashboard_content(request.user):
        raise PermissionDenied('Dashboard Content access requires the Content section.')
    return True


def visible_queryset(request, queryset, status_field='status'):
    if request.method not in ('GET', 'HEAD', 'OPTIONS') or dashboard_scope(request):
        return queryset
    return queryset.filter(**{status_field: PUBLIC_STATUS})
