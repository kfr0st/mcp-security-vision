"""Tests del tracker por IoU."""

from __future__ import annotations

from mcp_security_vision.detectors import Detection
from mcp_security_vision.tracker import SimpleTracker, iou


def test_iou_basics():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert 0 < iou((0, 0, 10, 10), (5, 0, 15, 10)) < 1


def test_same_object_keeps_id():
    tracker = SimpleTracker(iou_threshold=0.3)
    first = tracker.update([Detection("person", 0.9, (10, 10, 50, 90))], now=0.0)
    second = tracker.update([Detection("person", 0.9, (12, 12, 52, 92))], now=0.2)
    assert first[0].is_new is True
    assert second[0].is_new is False
    assert first[0].track_id == second[0].track_id


def test_two_objects_get_different_ids():
    tracker = SimpleTracker()
    tracked = tracker.update(
        [
            Detection("person", 0.9, (0, 0, 20, 40)),
            Detection("person", 0.8, (100, 0, 120, 40)),
        ],
        now=0.0,
    )
    ids = {item.track_id for item in tracked}
    assert len(ids) == 2


def test_label_change_creates_new_track():
    tracker = SimpleTracker()
    tracker.update([Detection("person", 0.9, (0, 0, 20, 40))], now=0.0)
    tracked = tracker.update([Detection("car", 0.9, (0, 0, 20, 40))], now=0.1)
    assert tracked[0].is_new is True
    assert tracked[0].track_id == 2


def test_expired_track_is_replaced():
    tracker = SimpleTracker(max_age_seconds=1.0)
    tracker.update([Detection("person", 0.9, (0, 0, 20, 40))], now=0.0)
    tracked = tracker.update([Detection("person", 0.9, (0, 0, 20, 40))], now=5.0)
    assert tracked[0].is_new is True
    assert len(tracker.tracks) == 1


def test_reset():
    tracker = SimpleTracker()
    tracker.update([Detection("person", 0.9, (0, 0, 20, 40))], now=0.0)
    tracker.reset()
    assert tracker.tracks == []
    assert tracker.update([Detection("person", 0.9, (0, 0, 20, 40))], now=0.1)[0].track_id == 1
