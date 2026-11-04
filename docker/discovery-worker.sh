#!/bin/sh
set -eu

cycles=0
while :; do
    python manage.py aggregate_discovery --batch 500 --max-batches 100
    if [ "$cycles" -eq 0 ]; then
        python manage.py cleanup_discovery --apply
    fi
    cycles=$(((cycles + 1) % 1440))
    sleep 60
done
