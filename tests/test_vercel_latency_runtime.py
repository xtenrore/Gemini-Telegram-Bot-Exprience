from pathlib import Path


# Regression coverage for v3.3 Telegram latency and shared Mongo lifetime.
RUNTIME = Path("vercel_runtime/plane_workflows.py")
FIXED_RUNTIME = Path("vercel_runtime/plane_workflows_fixed.py")
INGRESS = Path("vercel_runtime/api/index.py")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_telegram_hot_path_does_not_start_scheduler_per_update():
    text = _text(RUNTIME)
    assert "await application.start()" not in text
    assert "await application.stop()" in text  # generation teardown only
    assert "_telegram_application" in text


def test_hot_paths_skip_repeated_index_maintenance():
    text = _text(RUNTIME)
    assert text.count("ensure_indexes=False") >= 2
    assert "ensure_indexes=True" in text


def test_http_clients_do_not_log_telegram_token_urls_at_info():
    text = _text(RUNTIME)
    assert '"httpx", "httpcore", "telegram.request"' in text
    assert "setLevel(logging.WARNING)" in text


def test_latency_instrumentation_is_present_without_config_dump():
    base = _text(RUNTIME)
    fixed = _text(FIXED_RUNTIME)
    ingress = _text(INGRESS)
    assert "total_ms=" in base
    assert "source_ms=" in base
    assert "init_ms=" in base
    assert "runtime_ms=" in fixed
    assert "handler_ms=" in fixed
    assert "resume_ms=" in ingress
    assert "logger.info(config" not in base + fixed + ingress


def test_slow_photo_work_is_detached_from_ordered_telegram_queue():
    text = _text(RUNTIME)
    fixed = _text(FIXED_RUNTIME)
    assert "_should_detach_telegram_update" in text
    assert 'data.startswith("photo:")' in text
    assert '{"/photo", "/conditions"}' in text
    assert "telegram_slow_update_workflow_v33" in fixed
    assert "await start(telegram_slow_update_workflow_v33" in fixed
    assert "await process_telegram_update_v33" in fixed


def test_monitor_and_bootstrap_do_not_close_shared_mongo_client():
    text = _text(RUNTIME)
    configure = text.split("async def configure_telegram", 1)[1].split("async def process_telegram_update", 1)[0]
    monitor = text.split("async def monitor_cycle_step", 1)[1].split("async def telegram_slow_update_workflow", 1)[0]
    assert "close_db" not in configure
    assert "close_db" not in monitor


def test_v33_callback_is_acknowledged_by_webhook_response():
    text = _text(INGRESS)
    assert 'title="Plane? Telegram Bot v3.3"' in text
    assert '"version": "3.3"' in text
    assert '"method": "answerCallbackQuery"' in text
    assert '"callback_query_id": callback_id' in text
    assert "JSONResponse" in text


def test_v33_handler_does_not_double_answer_silent_callback():
    text = _text(FIXED_RUNTIME)
    assert "_install_preacked_callback_answer" in text
    assert "if not args and not text and not show_alert and not url" in text
    assert "return await original_answer(self, *args, **kwargs)" in text


def test_v33_normal_and_slow_updates_use_fast_processor():
    text = _text(FIXED_RUNTIME)
    assert text.count("await process_telegram_update_v33") >= 2
    assert "Telegram v3.3 update processed" in text
