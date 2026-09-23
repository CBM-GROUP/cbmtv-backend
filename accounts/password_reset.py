"""
Emailed-code password reset.

POST /api/accounts/password-reset/          {email}
POST /api/accounts/password-reset/confirm/  {email, code, new_password}

The request step answers identically whether or not the email belongs to an
account, so it cannot be used to discover who has one. The confirm step reports
every failure -- unknown email, wrong code, expired code, exhausted code -- as
the same generic "Invalid or expired code." for the same reason.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import PasswordResetCode, User

REQUEST_ACCEPTED = "If an account exists for that email, a reset code has been sent."
INVALID_CODE = "Invalid or expired code."


def generate_code():
    """Six digits, from a CSPRNG, zero-padded so every code has six."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _find_active_user(email):
    return User.objects.filter(email__iexact=email.strip(), is_active=True).first()


def issue_reset_code(user):
    """
    Create a fresh code for `user`, killing any older unused ones, and email it.

    Returns the plaintext code (tests use it; nothing else should).
    """
    code = generate_code()
    ttl = settings.PASSWORD_RESET_CODE_TTL_MINUTES

    with transaction.atomic():
        PasswordResetCode.objects.filter(user=user, used=False).update(used=True)
        PasswordResetCode.objects.create(
            user=user,
            code_hash=make_password(code),
            expires_at=timezone.now() + timedelta(minutes=ttl),
        )

    send_mail(
        subject="Your CBM TV password reset code",
        message=(
            f"Hi {user.name or 'there'},\n\n"
            f"Your CBM TV password reset code is {code}.\n\n"
            f"It expires in {ttl} minutes. If you did not ask to reset your "
            f"password, you can ignore this email.\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return code


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=12, trim_whitespace=True)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_reset'

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = _find_active_user(serializer.validated_data['email'])
        if user is not None:
            issue_reset_code(user)

        return Response({"detail": REQUEST_ACCEPTED}, status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_reset_confirm'

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        invalid = Response({"code": [INVALID_CODE]}, status=status.HTTP_400_BAD_REQUEST)

        user = _find_active_user(data['email'])
        if user is None:
            return invalid

        with transaction.atomic():
            reset = (
                PasswordResetCode.objects.select_for_update()
                .filter(
                    user=user,
                    used=False,
                    expires_at__gt=timezone.now(),
                    attempts__lt=PasswordResetCode.MAX_ATTEMPTS,
                )
                .first()
            )
            if reset is None:
                return invalid

            if not check_password(data['code'], reset.code_hash):
                reset.attempts += 1
                reset.save(update_fields=['attempts'])
                return invalid

            # The code is right. Validate the password only now, so a wrong
            # code never reveals anything about password rules for this user,
            # and a weak password does not burn the code -- the user can retry
            # with a stronger one.
            try:
                validate_password(data['new_password'], user=user)
            except DjangoValidationError as exc:
                return Response(
                    {"new_password": list(exc.messages)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.set_password(data['new_password'])
            user.save(update_fields=['password'])
            reset.used = True
            reset.save(update_fields=['used'])

        return Response({"detail": "Password has been reset."}, status=status.HTTP_200_OK)
