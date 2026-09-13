FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Exactly two processes: one FastAPI/Telegram process and one monitor worker.
# The old Procfile also launched `python -m app.main`, creating a second web
# server on the same port and a duplicate Telegram client.
CMD ["honcho", "start", "web", "worker"]
