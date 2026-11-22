from __future__ import annotations

from datetime import datetime, timedelta, timezone

from temporal_tracking import TemporalTracker, TemporalTrackingConfig, evaluate_geofence_rules


BASE = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
GEOFENCE = {
    "id": 7,
    "name": "Entrada Principal",
    "active": True,
    "points": [
        {"lat": 0.001, "lon": -0.001},
        {"lat": 0.001, "lon": 0.001},
        {"lat": -0.001, "lon": 0.001},
        {"lat": -0.001, "lon": -0.001},
    ],
}


def config(**overrides):
    values = {
        "min_frames_to_confirm_track": 3,
        "min_track_age_seconds_to_confirm": 4,
        "stationary_min_seconds": 10,
        "stationary_distance_threshold_pixels": 20,
        "same_area_centroid_threshold_pixels": 35,
        "tracking_lost_tolerance_seconds": 10,
        "max_missing_frames_before_stale": 2,
        "reassociation_max_seconds": 5,
        "reassociation_distance_pixels": 40,
        "alert_cooldown_seconds": 60,
    }
    values.update(overrides)
    return TemporalTrackingConfig(**values)


def detection(track_id="cam:pessoa:1", object_type="pessoa", x=100, y=100, lat=-23.0, lon=-46.0):
    return {
        "camera_id": "cam",
        "camera_name": "Camera",
        "timestamp": BASE.isoformat(),
        "object_type": object_type,
        "track_id": track_id,
        "track_score": 0.9,
        "confidence": 0.85,
        "geo": {"lat": lat, "lon": lon, "accuracy_m": 1},
        "bbox": {"x": x, "y": y, "w": 30, "h": 90},
        "distance_m": 8,
    }


def payload_at(seconds, detections):
    timestamp = BASE + timedelta(seconds=seconds)
    for item in detections:
        item["timestamp"] = timestamp.isoformat()
    return {
        "camera": {"id": "cam"},
        "timestamp": timestamp.isoformat(),
        "frame": {"mode": "yolo"},
        "detections": detections,
    }


def feed(tracker, seconds, detections, geofences=None):
    if geofences is None:
        geofences = [GEOFENCE]
    return tracker.process_frame(payload_at(seconds, detections), geofences, frame_id=seconds + 1)


def test_single_frame_stays_observed_without_alert():
    tracker = TemporalTracker(config())

    tracks = feed(tracker, 0, [detection()])

    assert tracks[0]["status"] == "observed"
    assert evaluate_geofence_rules(list(tracker.tracks.values()), [rule()]) == []


def test_few_frames_below_minimum_do_not_become_stationary():
    tracker = TemporalTracker(config(min_frames_to_confirm_track=4))

    feed(tracker, 0, [detection()])
    feed(tracker, 2, [detection()])
    tracks = feed(tracker, 4, [detection()])

    assert tracks[0]["status"] in {"observed", "confirmed"}
    assert tracks[0]["status"] != "stationary"


def test_confirmed_same_area_for_short_time_is_stationary_candidate():
    tracker = TemporalTracker(config(stationary_min_seconds=20))

    feed(tracker, 0, [detection()])
    feed(tracker, 3, [detection(x=102)])
    tracks = feed(tracker, 6, [detection(x=103)])

    assert tracks[0]["status"] == "stationary_candidate"


def test_same_area_low_movement_above_minimum_becomes_stationary():
    tracker = TemporalTracker(config(stationary_min_seconds=10))

    feed(tracker, 0, [detection()])
    feed(tracker, 3, [detection(x=102)])
    feed(tracker, 6, [detection(x=103)])
    tracks = feed(tracker, 20, [detection(x=104)])

    assert tracks[0]["status"] == "stationary"
    assert tracks[0]["stationary_duration_seconds"] >= 10


def test_moving_person_inside_geofence_does_not_trigger_stationary_alert():
    tracker = TemporalTracker(config())

    feed(tracker, 0, [detection(x=100)])
    feed(tracker, 5, [detection(x=180)])
    tracks = feed(tracker, 10, [detection(x=260)])

    assert tracks[0]["status"] == "moving"
    assert evaluate_geofence_rules(list(tracker.tracks.values()), [rule(max_stationary_seconds=1)]) == []


def test_stationary_car_outside_geofence_has_no_geofence_alert():
    tracker = TemporalTracker(config(stationary_min_seconds=5))

    feed(tracker, 0, [detection(track_id="cam:carro:1", object_type="carro", lat=-22.9, lon=-45.9)], [])
    feed(tracker, 5, [detection(track_id="cam:carro:1", object_type="carro", x=102, lat=-22.9, lon=-45.9)], [])
    tracks = feed(tracker, 12, [detection(track_id="cam:carro:1", object_type="carro", x=103, lat=-22.9, lon=-45.9)], [])

    assert tracks[0]["status"] == "stationary"
    assert evaluate_geofence_rules(list(tracker.tracks.values()), [rule(object_type="carro", max_stationary_seconds=1)]) == []


def test_stationary_car_inside_geofence_above_limit_creates_event():
    tracker = TemporalTracker(config(stationary_min_seconds=5))

    feed(tracker, 0, [detection(track_id="cam:carro:2", object_type="carro")])
    feed(tracker, 5, [detection(track_id="cam:carro:2", object_type="carro", x=102)])
    feed(tracker, 12, [detection(track_id="cam:carro:2", object_type="carro", x=103)])

    events = evaluate_geofence_rules(list(tracker.tracks.values()), [rule(object_type="carro", max_stationary_seconds=1)], BASE + timedelta(seconds=12), tracker.config)

    assert len(events) == 1
    assert events[0]["track_id"] == "cam:carro:2"
    assert events[0]["metadata"]["evidence"]["frames_used"] >= 3


def test_short_detection_gap_preserves_track_and_time():
    tracker = TemporalTracker(config())

    feed(tracker, 0, [detection(track_id="", x=100)])
    first_id = next(iter(tracker.tracks))
    feed(tracker, 2, [])
    feed(tracker, 4, [detection(track_id="", x=105)])

    assert len(tracker.tracks) == 1
    assert next(iter(tracker.tracks)) == first_id


def test_long_detection_gap_marks_track_stale_or_closed():
    tracker = TemporalTracker(config(tracking_lost_tolerance_seconds=5, max_missing_frames_before_stale=1))

    feed(tracker, 0, [detection()])
    feed(tracker, 8, [])

    assert tracker.get_track("cam:pessoa:1").status in {"stale", "closed"}


def test_cooldown_prevents_duplicate_events_each_frame():
    tracker = TemporalTracker(config(stationary_min_seconds=5, alert_cooldown_seconds=60))
    event_rule = rule(max_stationary_seconds=1, cooldown_seconds=60)

    feed(tracker, 0, [detection()])
    feed(tracker, 5, [detection(x=102)])
    feed(tracker, 12, [detection(x=103)])
    events_1 = evaluate_geofence_rules(list(tracker.tracks.values()), [event_rule], BASE + timedelta(seconds=12), tracker.config)
    feed(tracker, 15, [detection(x=104)])
    events_2 = evaluate_geofence_rules(list(tracker.tracks.values()), [event_rule], BASE + timedelta(seconds=15), tracker.config)

    assert len(events_1) == 1
    assert events_2 == []


def test_ambiguous_reassociation_creates_new_observed_track():
    tracker = TemporalTracker(config(reassociation_distance_pixels=80))

    feed(tracker, 0, [detection(track_id="cam:pessoa:a", x=100), detection(track_id="cam:pessoa:b", x=150)])
    feed(tracker, 2, [detection(track_id="", x=126)])

    assert len(tracker.tracks) == 3
    assert list(tracker.tracks.values())[-1].status == "observed"


def rule(object_type="pessoa", max_stationary_seconds=10, max_presence_seconds=0, cooldown_seconds=60):
    return {
        "id": 1,
        "geofence_id": 7,
        "name": "Regra temporal",
        "active": True,
        "object_type": object_type,
        "object_types": [object_type],
        "max_stationary_seconds": max_stationary_seconds,
        "max_presence_seconds": max_presence_seconds,
        "min_frames_required": 3,
        "min_confidence_required": 0,
        "active_time_start": "00:00",
        "active_time_end": "23:59",
        "active_days": [],
        "severity": "medium",
        "cooldown_seconds": cooldown_seconds,
        "message_template": "",
    }
