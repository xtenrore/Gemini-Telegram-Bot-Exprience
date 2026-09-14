from pathlib import Path


HEALTH_WORKFLOW = Path(".github/workflows/production-health-v33.yml")


def _text() -> str:
    return HEALTH_WORKFLOW.read_text(encoding="utf-8")


def test_health_watch_checks_monitor_freshness_and_generation():
    text = _text()
    assert 'monitor_age > 600' in text
    assert 'monitor.get("generation") == os.environ["GITHUB_SHA"]' in text
    assert 'runtime.get("generation") == os.environ["GITHUB_SHA"]' in text
    assert 'runtime.get("state") == "running"' in text


def test_health_watch_checks_telegram_delivery_backlog():
    text = _text()
    assert "getWebhookInfo" in text
    assert "pending > 25" in text
    assert "error_age < 900" in text


def test_health_watch_does_not_print_secret_values_or_error_bodies():
    text = _text()
    assert 'print(os.environ["TELEGRAM_BOT_TOKEN"])' not in text
    assert 'print(os.environ["MONGO_URI"])' not in text
    assert 'print(os.environ["GEMINI_API_KEY"])' not in text
    assert "exc.read()" not in text


def test_health_watch_runs_twice_per_hour_and_has_latency_budget():
    text = _text()
    assert 'cron: "7,37 * * * *"' in text
    assert "health_ms > 5000" in text
    assert "Deep verification latency" in text
