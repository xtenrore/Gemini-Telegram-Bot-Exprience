# ✈️ VM 2 — Dedicated High-Performance Monitoring Worker

Dedicated service running the **5-Second Aircraft Monitoring Loop** and **3D Curved Trajectory Predictor Engine**.

## 🚀 Features

- **Dedicated CPU/RAM**: 100% focused on querying aircraft APIs and computing flight trajectories.
- **Strict 1.5s Provider Timeout**: Never stalls if an API is slow, ensuring every 5-second cycle completes under 2.0s total.
- **Native 3D Curved Trajectory Predictor**: Extrapolates plane paths up to 3 minutes ahead over a +15km outer buffer.

---

## ⚙️ Render Deployment Instructions

1. Create a new **Background Worker** on Render from your repository.
2. Set **Root Directory**: `vm2`
3. Set **Runtime**: `Python`
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python worker.py`
6. Add Environment Variables:
   - `TELEGRAM_BOT_TOKEN`
   - `MONGO_URI`
   - `POLL_INTERVAL_SECONDS` = `5`
