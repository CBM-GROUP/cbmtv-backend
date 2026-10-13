from rest_framework.permissions import BasePermission


DASHBOARD_SECTIONS = ('channels', 'content', 'adverts', 'settings', 'users', 'analytics')


def dashboard_permissions(user):
    if not user or not user.is_authenticated or not user.is_active or user.must_change_password:
        return []
    if user.is_superuser or (user.is_staff and user.role in ('admin', 'internal_admin')):
        return list(DASHBOARD_SECTIONS)
    if user.dashboard_role_id:
        return [section for section in user.dashboard_role.permissions if section in DASHBOARD_SECTIONS]
    if user.is_staff:
        # Preserve legacy staff sections without silently granting the new analytics section.
        return [section for section in DASHBOARD_SECTIONS if section != 'analytics']
    return []


class HasDashboardSection(BasePermission):
    def has_permission(self, request, view):
        required = getattr(view, 'required_section', None)
        granted = dashboard_permissions(request.user)
        if isinstance(required, (list, tuple, set)):
            return any(section in granted for section in required)
        return required in granted


class IsDashboardSuperuser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_superuser and not user.must_change_password)
