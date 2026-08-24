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

# Ensure entrypoint script is executable
RUN chmod +x entrypoint.sh

# Run collectstatic during image build
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Execute entrypoint script
CMD ["./entrypoint.sh"]
