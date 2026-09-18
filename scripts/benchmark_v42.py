"""Small deterministic performance guard for the Plane Alerts v4.2 ensemble."""
from __future__ import annotations

import json
import time
import tracemalloc
from types import SimpleNamespace

from app.intelligence.route_guard_v42 import (
    _evaluate_motion_path,
    _motion_paths,
    _path_cache,
    reset_v42_state_for_tests,
)
from app.intelligence.route_history import AirportInfo


def main() -> None:
    reset_v42_state_for_tests()
    aircraft = SimpleNamespace(
        icao24="bench42",
        latitude=41.0,
        longitude=29.15,
        heading=270.0,
        ground_speed=260.0,
    )
    prediction = SimpleNamespace(turn_rate_deg_s=0.0)
    destination = AirportInfo(icao="LTFM", iata="IST", latitude=41.2753, longitude=28.7519)

    tracemalloc.start()
    started = time.perf_counter()
    paths = _motion_paths(
        ac=aircraft,
        pred=prediction,
        destination=destination,
        terminal_state="TERMINAL_ARRIVAL",
    )
    first_path_identity = id(paths)

    # 250 virtual users share the same aircraft-global motion paths. Only the
    # cheap observer-specific CPA reduction is repeated per user.
    evaluations = 0
    for index in range(250):
        observer_lat = 40.90 + (index % 25) * 0.008
        observer_lon = 28.75 + (index // 25) * 0.012
        reused = _motion_paths(
            ac=aircraft,
            pred=prediction,
            destination=destination,
            terminal_state="TERMINAL_ARRIVAL",
        )
        assert id(reused) == first_path_identity
        for path in reused:
            _evaluate_motion_path(
                path,
                observer_lat=observer_lat,
                observer_lon=observer_lon,
                radius_km=15.0,
            )
            evaluations += 1

    elapsed = time.perf_counter() - started
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report = {
        "users": 250,
        "motion_paths": len(paths),
        "observer_path_evaluations": evaluations,
        "elapsed_ms": round(elapsed * 1000.0, 3),
        "evaluations_per_second": round(evaluations / max(elapsed, 1e-9), 1),
        "current_kib": round(current_bytes / 1024.0, 1),
        "peak_kib": round(peak_bytes / 1024.0, 1),
        "path_cache_entries": len(_path_cache),
        "shared_path_reuse": True,
    }
    print("V42_BENCHMARK_JSON=" + json.dumps(report, sort_keys=True))

    assert len(paths) <= 9
    assert len(_path_cache) <= 128
    assert peak_bytes < 64 * 1024 * 1024
    assert elapsed < 10.0


if __name__ == "__main__":
    main()
