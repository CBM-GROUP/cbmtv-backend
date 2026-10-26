import secrets
import string
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import DashboardRole, User
from .email_identity import email_identity_guard
from .permissions import IsDashboardSuperuser, HasDashboardSection, dashboard_permissions
from .serializers import DashboardInviteSerializer, DashboardRoleSerializer, UserProfileSerializer


PIN_LENGTH = 16
PIN_ALPHABET = string.ascii_letters + string.digits
INVALID_PIN = 'Invalid or expired temporary PIN.'


def generate_temporary_pin():
    while True:
        pin = ''.join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))
        if any(char.isalpha() for char in pin) and any(char.isdigit() for char in pin):
            return pin


def set_temporary_pin(user):
    pin = generate_temporary_pin()
    user.set_password(pin)
    user.must_change_password = True
    user.temporary_pin_expires_at = timezone.now() + timedelta(hours=24)
    user.temporary_pin_attempts = 0
    user.save(update_fields=['password', 'must_change_password', 'temporary_pin_expires_at', 'temporary_pin_attempts'])
    return pin


def may_assign_role(caller, role):
    if caller.is_superuser or (caller.is_staff and caller.role in ('admin', 'internal_admin')):
        return True
    return set(role.permissions).issubset(set(dashboard_permissions(caller)))


def may_manage_user(caller, target):
    if target.is_superuser or (target.is_staff and target.role in ('admin', 'internal_admin')):
        return caller.is_superuser
    return not target.dashboard_role_id or may_assign_role(caller, target.dashboard_role)


def last_administrator(target):
    return (target.is_superuser or (target.is_staff and target.role in ('admin', 'internal_admin'))) and not User.objects.filter(
        Q(is_superuser=True) | Q(is_staff=True, role__in=('admin', 'internal_admin')),
        is_active=True,
    ).exclude(pk=target.pk).exists()


class DashboardRolesView(APIView):
    required_section = 'users'

    def get_permissions(self):
        if self.request.method == 'GET':
            return [HasDashboardSection()]
        return [IsDashboardSuperuser()]

    def get(self, request):
        roles = DashboardRole.objects.order_by('name')
        return Response(DashboardRoleSerializer(roles, many=True).data)

    def post(self, request):
        serializer = DashboardRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.save()
        return Response(DashboardRoleSerializer(role).data, status=status.HTTP_201_CREATED)


class DashboardRoleDetailView(APIView):
    permission_classes = [IsDashboardSuperuser]

    def patch(self, request, pk):
        role = DashboardRole.objects.filter(pk=pk).first()
        if role is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = DashboardRoleSerializer(role, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        role = DashboardRole.objects.filter(pk=pk).first()
        if role is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if User.objects.filter(dashboard_role=role).exists():
            return Response({'detail': 'Reassign users before deleting this role.'}, status=status.HTTP_409_CONFLICT)
        role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DashboardUsersView(APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'users'

    def get(self, request):
        users = User.objects.filter(Q(dashboard_role__isnull=False) | Q(is_staff=True)).distinct().select_related('dashboard_role').order_by('name')
        search = request.query_params.get('search', '').strip()
        if search:
            users = users.filter(Q(name__icontains=search) | Q(email__icontains=search))
        return Response(UserProfileSerializer(users, many=True).data)

    def post(self, request):
        serializer = DashboardInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data['dashboard_role']
        if not may_assign_role(request.user, role):
            return Response({'detail': 'You cannot assign permissions you do not have.'}, status=status.HTTP_403_FORBIDDEN)
        with email_identity_guard(serializer.validated_data['email']):
            serializer.validate_email(serializer.validated_data['email'])
            user = User.objects.create_user(
                email=serializer.validated_data['email'],
                name=serializer.validated_data['name'],
                password=None,
            )
            user.dashboard_role = role
            user.save(update_fields=['dashboard_role'])
            pin = set_temporary_pin(user)
        response = Response({**UserProfileSerializer(user).data, 'temporary_pin': pin}, status=status.HTTP_201_CREATED)
        response['Cache-Control'] = 'no-store'
        return response


class DashboardUserDetailView(APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'users'

    def get(self, request, pk):
        user = User.objects.filter(pk=pk).select_related('dashboard_role').first()
        if user is None or not (user.dashboard_role_id or user.is_staff):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(UserProfileSerializer(user).data)

    def patch(self, request, pk):
        allowed = {'name', 'email', 'phone', 'location', 'country', 'dashboard_role', 'is_active'}
        unexpected = set(request.data) - allowed
        if unexpected:
            return Response({'detail': f'Unsupported fields: {", ".join(sorted(unexpected))}.'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            # Serialize administrator deactivations so two concurrent requests
            # cannot each see the other as the remaining active administrator.
            list(User.objects.select_for_update().filter(
                Q(is_superuser=True) | Q(is_staff=True, role__in=('admin', 'internal_admin')),
                is_active=True,
            ).order_by('pk').values_list('pk', flat=True))
            user = User.objects.select_for_update().filter(pk=pk).select_related('dashboard_role').first()
            if user is None or not (user.dashboard_role_id or user.is_staff):
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not may_manage_user(request.user, user):
                return Response(status=status.HTTP_403_FORBIDDEN)
            if request.user.pk == user.pk and ('dashboard_role' in request.data or request.data.get('is_active') is False):
                return Response({'detail': 'You cannot change your own role or deactivate yourself.'}, status=status.HTTP_409_CONFLICT)
            if 'is_active' in request.data and request.data['is_active'] is False and last_administrator(user):
                return Response({'detail': 'The last administrator cannot be deactivated.'}, status=status.HTTP_409_CONFLICT)
            if 'is_active' in request.data and not isinstance(request.data['is_active'], bool):
                return Response({'is_active': ['Enter a valid boolean.']}, status=status.HTTP_400_BAD_REQUEST)
            if 'dashboard_role' in request.data:
                role = DashboardRole.objects.filter(pk=request.data['dashboard_role']).first()
                if role is None:
                    return Response({'dashboard_role': ['Choose a valid role.']}, status=status.HTTP_400_BAD_REQUEST)
                if not may_assign_role(request.user, role):
                    return Response({'detail': 'You cannot assign permissions you do not have.'}, status=status.HTTP_403_FORBIDDEN)
                user.dashboard_role = role
            detail = UserProfileSerializer(user, data={k: v for k, v in request.data.items() if k not in ('dashboard_role', 'is_active')}, partial=True)
            detail.is_valid(raise_exception=True)
            detail.save()
            if 'is_active' in request.data:
                user.is_active = request.data['is_active']
            user.save(update_fields=['dashboard_role', 'is_active'])
            return Response(UserProfileSerializer(user).data)


class ResendDashboardInviteView(APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'users'

    def post(self, request, pk):
        user = User.objects.filter(pk=pk, dashboard_role__isnull=False, must_change_password=True, is_active=True).first()
        if user is None:
            return Response({'detail': 'No pending invitation for this user.'}, status=status.HTTP_404_NOT_FOUND)
        if not may_assign_role(request.user, user.dashboard_role):
            return Response(status=status.HTTP_403_FORBIDDEN)
        pin = set_temporary_pin(user)
        response = Response({'temporary_pin': pin, 'detail': 'A new temporary PIN was issued. Share it securely now.'})
        response['Cache-Control'] = 'no-store'
        return response


class DashboardActivateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    temporary_pin = serializers.CharField(max_length=64, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class DashboardActivateView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'dashboard_activate'

    def post(self, request):
        serializer = DashboardActivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invalid = Response({'detail': INVALID_PIN}, status=status.HTTP_400_BAD_REQUEST)

        with email_identity_guard(data['email']):
            matches = list(User.objects.select_for_update().filter(email__iexact=data['email'].strip()).order_by('pk')[:2])
            user = matches[0] if len(matches) == 1 else None
            if user is None or not user.is_active or not user.dashboard_role_id or not user.must_change_password:
                return invalid
            if not user.temporary_pin_expires_at or user.temporary_pin_expires_at <= timezone.now() or user.temporary_pin_attempts >= 5:
                return invalid
            if not user.check_password(data['temporary_pin']):
                user.temporary_pin_attempts += 1
                user.save(update_fields=['temporary_pin_attempts'])
                return invalid
            if data['new_password'] == data['temporary_pin']:
                return Response({'new_password': ['Choose a different password.']}, status=status.HTTP_400_BAD_REQUEST)
            try:
                validate_password(data['new_password'], user=user)
            except DjangoValidationError as exc:
                return Response({'new_password': list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
            user.set_password(data['new_password'])
            user.must_change_password = False
            user.temporary_pin_expires_at = None
            user.temporary_pin_attempts = 0
            user.save(update_fields=['password', 'must_change_password', 'temporary_pin_expires_at', 'temporary_pin_attempts'])

        return Response({'detail': 'Password created. You can now sign in.'})
