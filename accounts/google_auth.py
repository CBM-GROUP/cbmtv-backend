"""Verify Google identity on the server before it can affect local accounts."""

from django.conf import settings
import ssl
import time
from google.auth import exceptions as google_exceptions
from google.auth.transport.urllib3 import Request
from google.oauth2 import id_token as google_id_token
from rest_framework.exceptions import AuthenticationFailed, APIException
import urllib3


class GoogleVerificationUnavailable(APIException):
    status_code = 503
    default_detail = 'Google sign-in is temporarily unavailable.'


def verified_google_identity(token):
    audiences = settings.GOOGLE_OAUTH_CLIENT_IDS
    if not audiences:
        raise GoogleVerificationUnavailable()

    try:
        # google-auth validates signature, expiration and issuer. Audience is
        # checked against our explicit allowlist below to support web/mobile IDs.
        # Use the operating system trust store so managed TLS roots work
        # without weakening certificate verification.
        http = urllib3.PoolManager(ssl_context=ssl.create_default_context())
        claims = google_id_token.verify_oauth2_token(token, Request(http), audience=None)
    except ValueError as exc:
        raise AuthenticationFailed('Invalid Google ID token.') from exc
    except google_exceptions.TransportError as exc:
        raise GoogleVerificationUnavailable() from exc
    except google_exceptions.GoogleAuthError as exc:
        raise AuthenticationFailed('Invalid Google ID token.') from exc

    if claims.get('aud') not in audiences or claims.get('iss') not in (
        'accounts.google.com', 'https://accounts.google.com',
    ) or not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= time.time():
        raise AuthenticationFailed('Invalid Google ID token.')
    subject = claims.get('sub')
    email = claims.get('email')
    if not isinstance(subject, str) or not subject.strip():
        raise AuthenticationFailed('Invalid Google ID token.')
    if not isinstance(email, str) or not email.strip() or claims.get('email_verified') is not True:
        raise AuthenticationFailed('A verified Google email is required.')
    return subject, email.strip(), claims.get('name', '')
