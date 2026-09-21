from django.db import connection
from django.http import JsonResponse, HttpResponse


HTML_LANDING = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CBMTV API &bull; Service Operational</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0b0f19;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
    }
    .card {
      background: rgba(17, 24, 39, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 16px;
      padding: 40px 32px;
      max-width: 500px;
      width: 100%;
      box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.7);
      text-align: center;
      backdrop-filter: blur(12px);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
      font-size: 13px;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 9999px;
      margin-bottom: 20px;
      letter-spacing: 0.02em;
    }
    .badge-dot {
      width: 8px;
      height: 8px;
      background: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 10px #10b981;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }
    h1 {
      font-size: 26px;
      font-weight: 700;
      color: #ffffff;
      margin-bottom: 10px;
      letter-spacing: -0.02em;
    }
    p.subtitle {
      font-size: 15px;
      color: #94a3b8;
      line-height: 1.5;
      margin-bottom: 28px;
    }
    .links {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 10px;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 12px 16px;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.2s ease;
    }
    .btn-primary {
      background: #3b82f6;
      color: #ffffff;
      border: 1px solid rgba(59, 130, 246, 0.5);
    }
    .btn-primary:hover {
      background: #2563eb;
      transform: translateY(-1px);
    }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.05);
      color: #cbd5e1;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.1);
      color: #ffffff;
      transform: translateY(-1px);
    }
    .footer {
      margin-top: 30px;
      padding-top: 18px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      font-size: 12px;
      color: #64748b;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">
      <span class="badge-dot"></span>
      Service is Running
    </div>
    <h1>CBMTV API</h1>
    <p class="subtitle">The backend service is online, healthy, and operational.</p>
    <div class="links">
      <a href="/api/schema/swagger-ui/" class="btn btn-primary">Swagger Docs</a>
      <a href="/api/schema/redoc/" class="btn btn-secondary">Redoc</a>
      <a href="/health/" class="btn btn-secondary">Health Check</a>
      <a href="/admin/" class="btn btn-secondary">Admin Portal</a>
    </div>
    <div class="footer">
      CBMTV Backend &bull; Operational
    </div>
  </div>
</body>
</html>
"""


def root_view(request):
    """Inform users that the service is running, with links to docs and health."""
    accept = request.META.get("HTTP_ACCEPT", "")
    if "text/html" in accept:
        return HttpResponse(HTML_LANDING, content_type="text/html")
    return JsonResponse({
        "service": "CBMTV API",
        "status": "running",
        "message": "The service is up and running.",
        "endpoints": {
            "health": "/health/",
            "admin": "/admin/",
            "swagger": "/api/schema/swagger-ui/",
            "redoc": "/api/schema/redoc/",
        }
    })


def health(request):
    """Report whether the API process and its database are ready."""
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})

