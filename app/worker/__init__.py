"""Background worker sub-package."""

import sys

# Keep unit-test imports side-effect free. Production imports of app.worker.*
# install the monitoring reliability guards before monitor.py binds its symbols.
if "pytest" not in sys.modules:
    from app.worker.reliability import install_reliability_guards

    install_reliability_guards()

    # Install the destination adapters first, then the non-blocking v2 route
    # resolver, and finally the v4.2 ensemble qualification guard. v4.2 does
    # not replace the trajectory engine; it constrains promotion of live CPA
    # candidates using deterministic terminal-arrival evidence and persistence.
    from app.intelligence.route_guard import install_route_guard
    from app.intelligence.route_guard_v2 import install_route_guard_v2
    from app.intelligence.route_guard_v42 import install_route_guard_v42

    install_route_guard()
    install_route_guard_v2()
    install_route_guard_v42()

    # Final hot-path protection: cap individual provider latency and prevent
    # stale ADS-B positions from creating brand-new approach alerts.
    from app.worker.critical_timing import install_critical_timing_guards

    install_critical_timing_guards()
