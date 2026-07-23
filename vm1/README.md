# 🌐 VM 1 — Web & Telegram Webhook Server

Dedicated service for hosting the **FastAPI Web Server**, **Telegram Webhook Receiver**, and **Admin Dashboard**.

## 🚀 Features

- **HTTPS Webhook Receiver (`POST /webhook`)**: Eliminates long-polling delays. Telegram sends updates instantly over HTTPS.
- **Render Health Check (`GET /`)**: Returns HTTP 200 OK plain text `"OK"` for deployment health checks and UptimeRobot tracking.
- **Web Admin Panel (`/admin`)**: Dashboard for system metrics and provider learning stats.

---

## ⚙️ Render Deployment Instructions

1. Create a new **Web Service** on Render from your repository.
2. Set **Root Directory**: `vm1`
3. Set **Runtime**: `Python`
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Set **Health Check Path**: `/`
7. Add Environment Variables:
   - `TELEGRAM_BOT_TOKEN`
   - `MONGO_URI`
   - `WEBHOOK_URL` = Your Render Service HTTPS URL (e.g. `https://your-vm1-app.onrender.com`)
