"""Deterministic Plane? v3.4 spotting-intelligence engines."""

# Production reliability guard: loaded once with the intelligence package so
# every route-gate evaluation gets the destination-near-observer protection.
from app.intelligence import arrival_guard_hotfix as _arrival_guard_hotfix  # noqa: F401,E402
