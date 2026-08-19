web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn procuremcp.wsgi --bind 0.0.0.0:$PORT
worker: celery -A procuremcp worker --loglevel=info
mcp: python manage.py run_mcp_server --transport sse --port 8001
