from pathlib import Path


def test_railway_docker_entrypoint_starts_v44_composition_layer() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (root / "scripts" / "railway-entrypoint.sh").read_text(encoding="utf-8")
    procfile = (root / "Procfile").read_text(encoding="utf-8")

    # Railway's Docker image executes railway-entrypoint.sh directly, so the
    # script must start the v4.4 composition layer. A Procfile-only change is
    # insufficient and previously left Telegram-visible v4.4 handlers disabled.
    assert 'CMD ["/app/scripts/railway-entrypoint.sh"]' in dockerfile
    assert "uvicorn app.main_v44:app" in entrypoint
    assert "uvicorn app.main:app" not in entrypoint
    assert "uvicorn app.main_v44:app" in procfile
