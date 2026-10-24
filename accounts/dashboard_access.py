import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import DashboardRole, User
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


def send_invitation(user, pin):
    # Production must send the PIN to the user, never write it to server logs.
    if not settings.DEBUG and settings.EMAIL_BACKEND in (
        'django.core.mail.backends.console.EmailBackend',
        'django.core.mail.backends.dummy.EmailBackend',
    ):
        raise RuntimeError('Outgoing email is not configured.')

    dashboard_url = getattr(settings, 'DASHBOARD_URL', 'https://cbmtv-dashboard-five.vercel.app').rstrip('/')
    send_mail(
        subject='Your CBM TV dashboard invitation',
        message=(
            f'Hi {user.name},\n\n'
            f'You have been invited to the CBM TV dashboard.\n'
            f'Open {dashboard_url}/activate and enter your email and temporary PIN: {pin}\n\n'
            f'The PIN expires in 24 hours and is only for setting your own password.\n'
            f'If you did not expect this invitation, ignore this email.\n'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


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
        return Response(UserProfileSerializer(users, many=True).data)

    def post(self, request):
        serializer = DashboardInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data['dashboard_role']
        if not may_assign_role(request.user, role):
            return Response({'detail': 'You cannot assign permissions you do not have.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=serializer.validated_data['email'],
                    name=serializer.validated_data['name'],
                    password=None,
                )
                user.dashboard_role = role
                user.save(update_fields=['dashboard_role'])
                pin = set_temporary_pin(user)
                send_invitation(user, pin)
        except Exception:
            return Response({'detail': 'Invitation could not be emailed. Check the outgoing email configuration.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(UserProfileSerializer(user).data, status=status.HTTP_201_CREATED)


class DashboardUserDetailView(APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'users'

    def patch(self, request, pk):
        user = User.objects.filter(pk=pk, dashboard_role__isnull=False).first()
        if user is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        role = DashboardRole.objects.filter(pk=request.data.get('dashboard_role')).first()
        if role is None:
            return Response({'dashboard_role': ['Choose a valid role.']}, status=status.HTTP_400_BAD_REQUEST)
        if not may_assign_role(request.user, role):
            return Response({'detail': 'You cannot assign permissions you do not have.'}, status=status.HTTP_403_FORBIDDEN)
        user.dashboard_role = role
        user.save(update_fields=['dashboard_role'])
        return Response(UserProfileSerializer(user).data)


class ResendDashboardInviteView(APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'users'

    def post(self, request, pk):
        user = User.objects.filter(pk=pk, dashboard_role__isnull=False, must_change_password=True).first()
        if user is None:
            return Response({'detail': 'No pending invitation for this user.'}, status=status.HTTP_404_NOT_FOUND)
        if not may_assign_role(request.user, user.dashboard_role):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            with transaction.atomic():
                pin = set_temporary_pin(user)
                send_invitation(user, pin)
        except Exception:
            return Response({'detail': 'Invitation could not be emailed.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'detail': 'A new temporary PIN was emailed.'})


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

        with transaction.atomic():
            user = User.objects.select_for_update().filter(email__iexact=data['email'].strip(), is_active=True).first()
            if user is None or not user.dashboard_role_id or not user.must_change_password:
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
