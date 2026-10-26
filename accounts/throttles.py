from django.conf import settings
from ipaddress import ip_address
from rest_framework.throttling import ScopedRateThrottle


class PasswordLoginThrottle(ScopedRateThrottle):
    """Ignore client-supplied forwarding headers unless proxy hops are configured."""

    def get_ident(self, request):
        hops = settings.PASSWORD_LOGIN_NUM_PROXIES
        peer = request.META.get('REMOTE_ADDR') or 'unknown'
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if hops > 0 and peer in settings.PASSWORD_LOGIN_TRUSTED_PROXY_IPS and forwarded:
            chain = forwarded.split(',')
            if len(chain) >= hops:
                try:
                    return str(ip_address(chain[-hops].strip()))
                except ValueError:
                    pass
        return peer
