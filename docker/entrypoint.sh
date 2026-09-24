#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python manage.py migrate --noinput
fi

# Optional first-boot superuser. Runs only when both vars are set, and NEVER
# touches an account that already exists -- `createsuperuser --noinput` errors
# on a duplicate email, and under `set -e` that would crash the container on
# every boot after the first. This is also why the whole block is `|| echo`:
# a bootstrap problem must not take the service down.
#
# It does not update an existing user's password. Rotate the password in the
# app, not by editing this env var, and delete DJANGO_SUPERUSER_PASSWORD from
# the host once the account exists.
if [ "${SEED_SUPERUSER:-true}" = "true" ]; then
    python manage.py seed_superadmin || echo "superuser bootstrap: skipped (non-fatal)"
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
