from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.next_hour_shadow import (
    _closest_point_in_expectation_window,
    _outcome_match_bounds,
    _validate_legacy_outcomes,
)


class _Cursor(list):
    def limit(self, value):
        return _Cursor(self[:value])


class _AuditCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection=None):
        found = []
        for doc in self.docs:
            if doc.get("kind") != query.get("kind"):
                continue
            if "match_validation" in query:
                exists = "match_validation" in doc
                if query["match_validation"].get("$exists") is False and exists:
                    continue
            if "actual_cpa_at" in query and query["actual_cpa_at"].get("$ne") is None:
                if doc.get("actual_cpa_at") is None:
                    continue
            found.append(doc)
        return _Cursor(found)

    def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    def update_one(self, query, update, upsert=False):
        target = self.find_one(query)
        if target is None:
            return SimpleNamespace(modified_count=0, upserted_id=None)
        target.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1, upserted_id=None)


class _Database(dict):
    pass


def _dt(hour, minute=0):
    return datetime(2026, 9, 19, hour, minute, tzinfo=timezone.utc)


def _point(at, lat, lon=0.0):
    return {"lat": lat, "lon": lon, "t": at.timestamp()}


def test_outcome_match_is_bounded_around_expected_occurrence():
    expectation = {
        "predicted_cpa_at": _dt(20, 27),
        "window_start": _dt(20, 17),
        "window_end": _dt(20, 37),
    }
    points = [
        # Wrong same-day occurrence: spatially closest but more than seven hours early.
        _point(_dt(13, 6), 0.001),
        # Correct occurrence inside the expected interval.
        _point(_dt(20, 29), 0.05),
    ]

    closest, sample_count, bounds = _closest_point_in_expectation_window(
        points, 0.0, 0.0, expectation
    )

    assert bounds == (_dt(20, 2), _dt(20, 52))
    assert sample_count == 1
    assert closest is not None
    _distance_km, actual_ts = closest
    assert datetime.fromtimestamp(actual_ts, timezone.utc) == _dt(20, 29)


def test_no_observation_in_expected_interval_does_not_fall_back_to_whole_day():
    expectation = {
        "predicted_cpa_at": _dt(20, 27),
        "window_start": _dt(20, 17),
        "window_end": _dt(20, 37),
    }
    points = [_point(_dt(13, 6), 0.001)]

    closest, sample_count, bounds = _closest_point_in_expectation_window(
        points, 0.0, 0.0, expectation
    )

    assert bounds is not None
    assert sample_count == 0
    assert closest is None


def test_legacy_expectation_without_window_is_still_bounded():
    expectation = {"predicted_cpa_at": _dt(20, 27)}
    bounds = _outcome_match_bounds(expectation)

    assert bounds is not None
    start, end = bounds
    assert start == _dt(19, 42)
    assert end == _dt(21, 12)


def test_wrong_occurrence_legacy_outcome_is_quarantined_not_scored():
    key = "1:QNT576:2026-09-19"
    expectation = {
        "_id": "exp-1",
        "kind": "next_hour_expectation",
        "expectation_key": key,
        "predicted_cpa_at": _dt(20, 27),
        "window_start": _dt(20, 17),
        "window_end": _dt(20, 37),
        "status": "resolved",
    }
    outcome = {
        "_id": "out-1",
        "kind": "next_hour_outcome",
        "expectation_key": key,
        "actual_observed": True,
        "actual_pass": True,
        "actual_closest_km": 4.2,
        "actual_cpa_at": _dt(13, 6),
        "timing_error_s": -26460.0,
    }
    audit = _AuditCollection([expectation, outcome])
    database = _Database(prediction_lab_audit=audit)

    quarantined = _validate_legacy_outcomes(database, _dt(22, 0))

    assert quarantined == 1
    assert outcome["match_validation"] == "quarantined_wrong_occurrence"
    assert outcome["resolution"] == "unresolved_occurrence_match"
    assert outcome["actual_pass"] is None
    assert outcome["actual_observed"] is False
    assert outcome["timing_error_s"] is None
    assert outcome["legacy_actual_pass"] is True
    assert outcome["legacy_actual_cpa_at"] == _dt(13, 6)
    assert expectation["status"] == "unresolved_occurrence_match"


def test_legacy_outcome_inside_match_window_remains_scored():
    key = "1:THY123:2026-09-19"
    expectation = {
        "_id": "exp-2",
        "kind": "next_hour_expectation",
        "expectation_key": key,
        "predicted_cpa_at": _dt(20, 27),
        "window_start": _dt(20, 17),
        "window_end": _dt(20, 37),
        "status": "resolved",
    }
    outcome = {
        "_id": "out-2",
        "kind": "next_hour_outcome",
        "expectation_key": key,
        "actual_observed": True,
        "actual_pass": False,
        "actual_closest_km": 18.0,
        "actual_cpa_at": _dt(20, 40),
        "timing_error_s": 780.0,
    }
    audit = _AuditCollection([expectation, outcome])
    database = _Database(prediction_lab_audit=audit)

    quarantined = _validate_legacy_outcomes(database, _dt(22, 0))

    assert quarantined == 0
    assert outcome["match_validation"] == "bounded_window_legacy_validated"
    assert outcome["actual_pass"] is False
    assert outcome["timing_error_s"] == 780.0
    assert expectation["status"] == "resolved"
