FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONMALLOC=malloc

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/scripts/railway-entrypoint.sh

EXPOSE 8000

# One Python interpreter runs FastAPI, Telegram, and the integrated ADS-B monitor.
# On Railway the entrypoint first verifies that Telegram, MongoDB, and Gemini
# credentials are actually present so a broken deployment cannot appear healthy.
CMD ["/app/scripts/railway-entrypoint.sh"]
