"""Background worker sub-package."""

import sys

# Keep unit-test imports side-effect free. Production imports of app.worker.*
# install the monitoring reliability guards before monitor.py binds its symbols.
if "pytest" not in sys.modules:
    from app.worker.reliability import install_reliability_guards

    install_reliability_guards()

    # Install after the general reliability guards so this destination-aware
    # evaluator is the final RouteHistoryService implementation used by the
    # live monitor. It prevents straight-line CPA false positives from arrivals
    # that are about to turn toward a known destination airport.
    from app.intelligence.route_guard import install_route_guard

    install_route_guard()
