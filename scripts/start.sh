#!/bin/sh
# Starts the server container (the Dockerfile's CMD). See docs/project/DEPLOY.md.
#
# Every start: apply migrations, optionally load the demo hotels and create the admin
# account, then hand the process to gunicorn. Render gives the port in $PORT.
set -e

python manage.py migrate --noinput

case "${SEED_ON_START:-false}" in
    true|True|TRUE|1)
        # seed_demo does nothing when the demo hotels already exist
        python manage.py seed_demo || echo "seed_demo failed: starting without demo data"
        ;;
esac

# The admin account comes from DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD,
# which createsuperuser reads by itself; it fails harmlessly once the account exists
if [ -n "${DJANGO_SUPERUSER_EMAIL:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    python manage.py createsuperuser --noinput \
        || echo "createsuperuser skipped: the admin account probably exists already"
fi

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout 60 \
    --access-logfile -
