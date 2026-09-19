from pathlib import Path

from app.agy_permission_guard_v422 import (
    output_has_permission_denial,
    permission_retry_delay_s,
)


def test_permission_denial_detection_matches_real_headless_result():
    lines = [
        '[OUT] {"event":"result","result":{"status":"SUCCESS","denied_actions":[{"action":"command"}]}}'
    ]
    assert output_has_permission_denial(lines) is True


def test_permission_denial_detection_matches_tool_error():
    lines = [
        '[OUT] {"event":"step_update","step_update":{"state":"ERROR","tool_info":{"error":{"message":"permission check failed for unsandboxed cat heredoc"}}}}'
    ]
    assert output_has_permission_denial(lines) is True
    assert output_has_permission_denial(['[OUT] normal successful python3 output']) is False


def test_permission_retry_backoff_is_fast_then_bounded():
    assert permission_retry_delay_s(1) == 15
    assert permission_retry_delay_s(2) == 30
    assert permission_retry_delay_s(3) == 60
    assert permission_retry_delay_s(10) <= 300


def test_agy_worker_installs_permission_recovery_before_startup():
    source = Path('app/agy_worker_ext.py').read_text()
    assert 'install_permission_guard_v422()' in source
    assert 'from app.agy_permission_guard_v422 import install_permission_guard_v422' in source


def test_persisted_tooling_policy_forces_supported_file_workflow():
    source = Path('scripts/agy-worker-entrypoint.sh').read_text()
    assert 'NEVER use run_command to create or modify a file' in source
    assert 'write_to_file first' in source
    assert 'tooling_policy_version = 2' in source
    assert "supervisor['next_run_at'] = 0" in source
    assert '--dangerously-skip-permissions' not in source


def test_shadow_scans_no_longer_block_context_and_handoff_loop():
    source = Path('scripts/agy_bridge_daemon.py').read_text()
    assert 'ThreadPoolExecutor(max_workers=1' in source
    assert 'executor.submit(update_next_hour_shadow)' in source
    assert 'executor.submit(update_sentinel_shadow)' in source
    assert 'build_context_snapshot()' in source
    assert 'sync_findings_to_handoff()' in source
    assert 'counters = update_next_hour_shadow()' not in source
    assert 'counters = update_sentinel_shadow()' not in source


def test_denied_action_is_reclassified_instead_of_hourly_completed():
    source = Path('app/agy_permission_guard_v422.py').read_text()
    assert 'self.last_status = "permission_retry"' in source
    assert 'self.next_run_at = time.time() + delay' in source
    assert 'self.last_status != "quota_wait"' in source
