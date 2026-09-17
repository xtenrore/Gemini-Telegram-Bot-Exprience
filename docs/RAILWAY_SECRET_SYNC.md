# Railway secret sync

Plane? runtime secrets live in Railway, while some provider credentials are entered as GitHub Actions secrets. The `Sync Plane secrets to Railway` workflow bridges only the approved provider variables into the production Railway service.

Synced names:

- `OPENSKY_1` through `OPENSKY_5`
- `GEMINI_API_KEY`
- `GEMINI_API_KEY_2`
- `GROQ_KEY`
- `GROQ_KEY_2`

Authentication uses either the repository secret `RAILWAY_TOKEN` (preferred project token) or `RAILWAY_API_TOKEN`. Empty provider secrets are skipped. The workflow never intentionally echoes provider secret values and verifies only variable names after the update.

The workflow runs automatically when it is first added/changed on `main`, and can be re-run later with `workflow_dispatch` after provider secret changes.
