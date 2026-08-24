FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Install system dependencies (libpq for PostgreSQL/pgvector and curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . .

# Run collectstatic during image build
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Execute database migrations on container start, then boot Gunicorn with worker concurrency
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn procuremcp.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 120"]
