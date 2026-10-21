#!/bin/sh
set -eu

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
