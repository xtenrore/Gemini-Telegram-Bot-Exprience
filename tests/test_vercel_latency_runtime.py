from pathlib import Path


RUNTIME = Path("vercel_runtime/plane_workflows.py")


def _runtime_text() -> str:
    return RUNTIME.read_text(encoding="utf-8")


def test_telegram_hot_path_does_not_start_scheduler_per_update():
    text = _runtime_text()
    assert "await application.start()" not in text
    assert "await application.stop()" in text  # generation teardown only
    assert "_telegram_application" in text


def test_hot_paths_skip_repeated_index_maintenance():
    text = _runtime_text()
    assert text.count("ensure_indexes=False") >= 2
    assert "ensure_indexes=True" in text


def test_http_clients_do_not_log_telegram_token_urls_at_info():
    text = _runtime_text()
    assert '"httpx", "httpcore", "telegram.request"' in text
    assert "setLevel(logging.WARNING)" in text


def test_latency_instrumentation_is_present_without_config_dump():
    text = _runtime_text()
    assert "total_ms=" in text
    assert "source_ms=" in text
    assert "init_ms=" in text
    assert "logger.info(config" not in text


def test_slow_photo_work_is_detached_from_ordered_telegram_queue():
    text = _runtime_text()
    assert "_should_detach_telegram_update" in text
    assert 'data.startswith("photo:")' in text
    assert '{"/photo", "/conditions"}' in text
    assert "telegram_slow_update_workflow" in text
    assert "await start(telegram_slow_update_workflow" in text
    assert "await process_telegram_update" in text
