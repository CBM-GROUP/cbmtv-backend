#!/bin/sh
set -eu

if [ "${RAILWAY_DB_TUNNEL_ENABLED:-false}" = "true" ]; then
    : "${RAILWAY_DB_SSH_KEY_B64:?Set the temporary Railway SSH key}"
    : "${RAILWAY_DB_SSH_TARGET:?Set the private PostgreSQL address}"
    : "${RAILWAY_DB_SSH_USER:?Set the restricted SSH user}"
    : "${RAILWAY_DB_SSH_HOST:?Set the SSH host}"
    : "${DATABASE_URL:?Set the read-only database URL}"

    umask 077
    mkdir -p /tmp/railway-db-tunnel
    printf '%s' "$RAILWAY_DB_SSH_KEY_B64" | base64 -d > /tmp/railway-db-tunnel/key
    chmod 600 /tmp/railway-db-tunnel/key
    if [ "${RAILWAY_DB_TUNNEL_DIAGNOSTIC:-false}" = "true" ]; then
        python - <<'PY'
import socket

for label, host, port in (
    ('github_ssh_22', 'github.com', 22),
    ('github_ssh_443', 'ssh.github.com', 443),
    ('ec2_ssh_22', 'apicbmtv.cbmgroupco.com', 22),
):
    try:
        with socket.create_connection((host, port), timeout=5):
            print(f'{label}=connected', flush=True)
    except OSError:
        print(f'{label}=unreachable', flush=True)
PY
    fi
    ssh -N -T \
        -i /tmp/railway-db-tunnel/key \
        -o BatchMode=yes \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile=/app/docker/railway_known_hosts \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=3 \
        -L "127.0.0.1:15432:${RAILWAY_DB_SSH_TARGET}:5432" \
        "${RAILWAY_DB_SSH_USER}@${RAILWAY_DB_SSH_HOST}" &
    tunnel_pid=$!
    python - <<'PY'
import os
import time
import psycopg2

for _ in range(20):
    try:
        connection = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=2)
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        connection.close()
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit('Railway read-only database tunnel is unavailable.')
PY
    kill -0 "$tunnel_pid"
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python manage.py migrate --noinput
fi

# Bootstrap only with explicit credentials. A failed seed must stop deployment
# so a missing admin account cannot be mistaken for a successful release.
if [ "${SEED_SUPERUSER:-true}" = "true" ]; then
    if [ -n "${DJANGO_SUPERUSER_EMAIL:-}" ] || [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
        python manage.py seed_superadmin
    fi
fi

if [ "${COLLECT_STATIC:-true}" = "true" ]; then
    python manage.py collectstatic --noinput
fi

if [ "$#" -eq 0 ]; then
    set -- gunicorn cbmtv.wsgi:application
fi

if [ "$1" = "gunicorn" ]; then
    set -- "$@" \
        --bind "0.0.0.0:${PORT:-8000}" \
        --workers "${GUNICORN_WORKERS:-2}" \
        --threads "${GUNICORN_THREADS:-2}" \
        --timeout "${GUNICORN_TIMEOUT:-60}" \
        --access-logfile - \
        --error-logfile -
fi

exec "$@"
