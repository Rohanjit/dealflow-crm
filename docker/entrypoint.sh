#!/bin/sh
set -e
case "$1" in
  web)
    python manage.py migrate --noinput
    exec gunicorn dealflow.wsgi --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-3}" --timeout 300 --access-logfile -
    ;;
  scheduler)
    # Wait for the web container to finish migrations before the first check.
    sleep 20
    exec python manage.py run_scheduler
    ;;
  *)
    exec python manage.py "$@"
    ;;
esac
