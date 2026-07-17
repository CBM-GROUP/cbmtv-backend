#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python manage.py migrate --noinput
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
