"""Background worker sub-package."""

import sys

# Keep unit-test imports side-effect free. Production imports of app.worker.*
# install the monitoring reliability guards before monitor.py binds its symbols.
if "pytest" not in sys.modules:
    from app.worker.reliability import install_reliability_guards

    install_reliability_guards()

    # Install the existing destination adapters first, then the v2 guard which
    # moves route-network work off the live alert path and catches preterminal
    # airport turns that the original terminal-only geometry could miss.
    from app.intelligence.route_guard import install_route_guard
    from app.intelligence.route_guard_v2 import install_route_guard_v2

    install_route_guard()
    install_route_guard_v2()

    # Final ADS-B hot-path protection: cap individual provider latency and
    # prevent stale positions from creating brand-new approach alerts.
    from app.worker.critical_timing import install_critical_timing_guards

    install_critical_timing_guards()

    # Telegram must be the first external side effect once an alert qualifies.
    # Durable snapshot/history writes continue in bounded background tasks so a
    # slow MongoDB operation cannot make a physically correct alert arrive late.
    from app.worker.notification_fastpath import install_notification_fastpath

    install_notification_fastpath()
