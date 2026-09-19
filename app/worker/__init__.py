"""Background worker sub-package."""

import sys

# Keep unit-test imports side-effect free. Production imports of app.worker.*
# install the monitoring reliability guards before monitor.py binds its symbols.
if "pytest" not in sys.modules:
    from app.worker.reliability import install_reliability_guards

    install_reliability_guards()

    # Install the midpoint trajectory integrator before monitor.py imports
    # predict_trajectory by name. This keeps the robust v3.4/v4.2 predictor but
    # removes the systematic full-step acceleration/turn integration bias.
    from app.intelligence.trajectory_hotfix_v43 import install_trajectory_hotfix_v43

    install_trajectory_hotfix_v43()

    # Install the destination adapters first, then the non-blocking v2 route
    # resolver, the v4.2 ensemble qualification guard, and finally the v4.3
    # confirmed-cancellation latch. The latch only prevents predictive jitter
    # from immediately resurrecting a cancelled encounter; fresh physical entry
    # into the configured radius remains authoritative.
    from app.intelligence.route_guard import install_route_guard
    from app.intelligence.route_guard_v2 import install_route_guard_v2
    from app.intelligence.route_guard_v42 import install_route_guard_v42
    from app.intelligence.requalification_guard_v43 import install_requalification_guard_v43

    install_route_guard()
    install_route_guard_v2()
    install_route_guard_v42()
    install_requalification_guard_v43()

    # Final hot-path protection: cap individual provider latency and prevent
    # stale ADS-B positions from creating brand-new approach alerts.
    from app.worker.critical_timing import install_critical_timing_guards

    install_critical_timing_guards()
