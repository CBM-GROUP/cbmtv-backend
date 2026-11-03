from django.utils.cache import patch_vary_headers


class PrivateResponseMixin:
    """Personalized responses and errors must never be shared across viewers."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response
