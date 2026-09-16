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
if [ -n "${DJANGO_SUPERUSER_EMAIL:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    python manage.py shell <<'PYEOF' || echo "superuser bootstrap: skipped (non-fatal)"
import os
from django.contrib.auth import get_user_model

User = get_user_model()
email = os.environ["DJANGO_SUPERUSER_EMAIL"]
if User.objects.filter(email=email).exists():
    print("superuser bootstrap: %s already exists, left untouched" % email)
else:
    # accounts.User.REQUIRED_FIELDS = name, phone, location, country
    User.objects.create_superuser(
        email=email,
        name=os.environ.get("DJANGO_SUPERUSER_NAME", "CBM TV Admin"),
        phone=os.environ.get("DJANGO_SUPERUSER_PHONE", ""),
        location=os.environ.get("DJANGO_SUPERUSER_LOCATION", ""),
        country=os.environ.get("DJANGO_SUPERUSER_COUNTRY", ""),
        password=os.environ["DJANGO_SUPERUSER_PASSWORD"],
    )
    print("superuser bootstrap: created %s (role=admin, staff, superuser)" % email)
PYEOF
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
