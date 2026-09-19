from concurrent.futures import Future
from pathlib import Path

from app.agy_shadow_runner import SingleFlightShadowAudits


class ControlledExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, function):
        future = Future()
        self.submissions.append((function, future))
        return future


def test_shadow_audits_are_single_flight_and_oldest_due_job_is_fair():
    executor = ControlledExecutor()
    next_hour = lambda: {"created": 1}
    sentinel = lambda: {"created": 2}
    runner = SingleFlightShadowAudits(
        next_hour_fn=next_hour,
        sentinel_fn=sentinel,
        next_hour_interval_s=60.0,
        sentinel_interval_s=120.0,
        executor=executor,
    )

    assert runner.poll(0.0) == []
    assert len(executor.submissions) == 1
    assert executor.submissions[0][0] is next_hour
    assert runner.busy

    # Polling while the first database job is blocked must not create another
    # thread/job or wait for it to finish.
    assert runner.poll(1.0) == []
    assert len(executor.submissions) == 1

    executor.submissions[0][1].set_result({"created": 1, "resolved": 2})
    events = runner.poll(3.0)
    assert len(events) == 1
    assert events[0].kind == "next_hour"
    assert events[0].ok
    assert events[0].counters["resolved"] == 2

    # Sentinel was also due at startup. Once next-hour completes it gets the
    # single slot before the next next-hour interval, preventing starvation.
    assert len(executor.submissions) == 2
    assert executor.submissions[1][0] is sentinel


def test_shadow_audit_exception_becomes_event_instead_of_escaping_bridge_loop():
    executor = ControlledExecutor()
    runner = SingleFlightShadowAudits(
        next_hour_fn=lambda: {},
        sentinel_fn=lambda: {},
        executor=executor,
    )

    runner.poll(0.0)
    executor.submissions[0][1].set_exception(TimeoutError("mongo read timed out"))
    events = runner.poll(3.0)

    assert len(events) == 1
    assert events[0].kind == "next_hour"
    assert not events[0].ok
    assert events[0].error_type == "TimeoutError"
    assert "mongo read timed out" in events[0].error_message


def test_bridge_prioritizes_context_and_handoff_before_shadow_polling():
    source = Path("scripts/agy_bridge_daemon.py").read_text()
    context_call = source.index("                    build_context_snapshot()")
    handoff_call = source.index("                published = sync_findings_to_handoff()")
    shadow_poll = source.index("            for event in shadow.poll(now):")

    assert context_call < shadow_poll
    assert handoff_call < shadow_poll
    assert "logger.exception(\"Next-hour shadow audit failed\")" not in source
    assert "logger.exception(\"Europe sentinel shadow audit failed\")" not in source


def test_shadow_runner_uses_one_worker_only():
    source = Path("app/agy_shadow_runner.py").read_text()
    assert "max_workers=1" in source
