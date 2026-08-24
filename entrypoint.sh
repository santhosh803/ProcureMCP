#!/bin/sh
set -e

echo "=== [ProcureMCP] Running database migrations ==="
python manage.py migrate --noinput

echo "=== [ProcureMCP] Starting Gunicorn on port ${PORT:-8000} ==="
exec gunicorn procuremcp.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -
