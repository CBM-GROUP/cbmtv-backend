from django.db import connection
from django.http import JsonResponse


def health(request):
    """Report whether the API process and its database are ready."""
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})
