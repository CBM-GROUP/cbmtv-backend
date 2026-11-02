"""Account-wide JWT revocation, compatible with tokens issued before this field existed."""

from django.db.models import F
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User


def validate_session(token, user):
    version = token.get('session_version', 0)
    if type(version) is not int or version != user.session_version or user.must_change_password:
        raise AuthenticationFailed('Session expired. Please sign in again.', code='session_revoked')


def revoke_sessions(user):
    # Increment in the database so concurrent logout/password changes cannot
    # restore an earlier version. Password callers use the same transaction.
    User.objects.filter(pk=user.pk).update(session_version=F('session_version') + 1)


class SessionJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        validate_session(validated_token, user)
        return user


class SessionRefreshToken(RefreshToken):
    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        token['session_version'] = user.session_version
        return token
