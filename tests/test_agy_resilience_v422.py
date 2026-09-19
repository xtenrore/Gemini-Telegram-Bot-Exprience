from pathlib import Path

from app.agy_tool_recovery_v431 import (
    extract_conversation_id,
    output_has_permission_denial,
    recovery_prompt,
)


def test_permission_denial_detection_matches_real_headless_result():
    lines = [
        '[OUT] {"event":"result","result":{"conversation_id":"conv-123","status":"SUCCESS","denied_actions":[{"action":"command"}]}}'
    ]
    assert output_has_permission_denial(lines) is True


def test_permission_denial_detection_matches_tool_error():
    lines = [
        '[OUT] {"event":"step_update","step_update":{"state":"ERROR","tool_info":{"error":{"message":"permission check failed for unsandboxed cat heredoc"}}}}'
    ]
    assert output_has_permission_denial(lines) is True
    assert output_has_permission_denial(['[OUT] normal successful python3 output']) is False


def test_conversation_id_is_recovered_from_result_init_and_truncated_events():
    result_lines = [
        '[OUT] {"event":"result","result":{"conversation_id":"result-conv","status":"SUCCESS"}}'
    ]
    init_lines = [
        '[OUT] {"event":"init","conversation_id":"init-conv","init":{"cwd":"/app"}}'
    ]
    truncated_lines = [
        '[OUT] {"event":"result","result":{"conversation_id":"truncated-conv","status":"SUCCESS","response":"' + ('x' * 5000)
    ]
    assert extract_conversation_id(result_lines) == 'result-conv'
    assert extract_conversation_id(init_lines) == 'init-conv'
    assert extract_conversation_id(truncated_lines) == 'truncated-conv'


def test_tool_recovery_guidance_escalates_without_widening_permissions():
    first = recovery_prompt(1)
    second = recovery_prompt(2)
    third = recovery_prompt(3)

    assert 'same audit' in first
    assert 'write_to_file' in first
    assert 'python3 /path/to/script.py' in first
    assert 'TOOLING RECOVERY MODE' in second
    assert 'Do not use run_command for file inspection' in second
    assert 'FINAL TOOLING RECOVERY MODE' in third
    assert 'stop using run_command for inspection' in third

    for prompt in (first, second, third):
        assert '--dangerously-skip-permissions' not in prompt
        assert 'cat' in prompt
        assert 'find' in prompt
        assert 'sed' in prompt
        assert 'head' in prompt
        assert 'tail' in prompt


def test_agy_worker_installs_same_conversation_recovery_before_startup():
    source = Path('app/agy_worker_ext.py').read_text()
    assert 'install_tool_recovery_v431()' in source
    assert 'from app.agy_tool_recovery_v431 import install_tool_recovery_v431' in source


def test_persisted_tooling_policy_forces_supported_file_workflow():
    source = Path('scripts/agy-worker-entrypoint.sh').read_text()
    assert 'NEVER use run_command to create or modify a file' in source
    assert 'write_to_file first' in source
    assert 'tooling_policy_version = 3' in source
    assert 'find, head, tail' in source
    assert 'resume the same' in source
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


def test_denied_action_resumes_exact_conversation_instead_of_backing_off():
    source = Path('app/agy_tool_recovery_v431.py').read_text()
    assert '"--conversation"' in source
    assert 'completed_after_tool_recovery' in source
    assert 'tool_recovery_exhausted' in source
    assert '_MAX_RECOVERY_TURNS = 4' in source
    assert 'tool_recovery_mode' in source
    assert 'tool_recovery_count' in source
    assert 'permission_retry_delay_s' not in source
    assert '--dangerously-skip-permissions' not in source
