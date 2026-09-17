"""Background worker sub-package."""

import sys

# Keep unit-test imports side-effect free. Production imports of app.worker.*
# install the monitoring reliability guards before monitor.py binds its symbols.
if "pytest" not in sys.modules:
    from app.worker.reliability import install_reliability_guards

    install_reliability_guards()
