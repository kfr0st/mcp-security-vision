"""Tests del pipeline de análisis (detección -> zonas -> eventos)."""

from __future__ import annotations

import numpy as np
import pytest

from mcp_security_vision.database import Database, EventType
from mcp_security_vision.detectors import Detection
from mcp_security_vision.faces import FaceRecognizer, FaceRegistry, FakeFaceBackend
from mcp_security_vision.fakes import FakeDetector
from mcp_security_vision.pipeline import EventThrottle, VisionPipeline
from mcp_security_vision.snapshots import SnapshotStore
from mcp_security_vision.zones import ZoneStore

SQUARE = [[0.30, 0.20], [0.70, 0.20], [0.70, 0.95], [0.30, 0.95]]


def _build(tmp_path, settings, detector=None, with_faces=True, with_snapshot=True):
    db = Database(tmp_path / "db.sqlite")
    zones = ZoneStore(db)
    zones.seed()
    registry = FaceRegistry(settings.known_faces_dir, FakeFaceBackend(), db=db)
    registry.load()
    recognizer = FaceRecognizer(registry, FakeFaceBackend()) if with_faces else None
    pipeline = VisionPipeline(
        settings=settings,
        db=db,
        detector=detector or FakeDetector(),
        face_recognizer=recognizer,
        zone_store=zones,
        snapshot_store=SnapshotStore(settings.snapshots_dir) if with_snapshot else None,
    )
    return pipeline, db, zones, registry


def test_analysis_without_zones_detects_objects(tmp_path, settings, frame):
    pipeline, _db, _zones, _registry = _build(tmp_path, settings)
    result = pipeline.analyze("cam1", frame)

    assert result.object_summary == "1 chair, 1 person"
    assert result.face_names == []  # el estabilizador necesita 2 votos
    # Hay persona pero aún no hay rostro estabilizado -> se registra como no identificada
    assert [event.event_type for event in result.events] == [EventType.PERSON_UNIDENTIFIED]


def test_face_known_after_registration(tmp_path, settings, frame):
    pipeline, db, _zones, registry = _build(tmp_path, settings)
    registry.register_from_frame("Ana Torres", frame)

    pipeline.analyze("cam1", frame)
    result = pipeline.analyze("cam1", frame)  # segunda lectura: nombre estabilizado

    assert "Ana Torres" in result.face_names
    events = db.get_events(event_type=EventType.FACE_KNOWN)
    assert len(events) == 1
    assert "Ana Torres" in events[0].description


def test_unknown_face_event_and_snapshot(tmp_path, settings, frame):
    pipeline, db, _zones, _registry = _build(tmp_path, settings)
    pipeline.analyze("cam1", frame)
    result = pipeline.analyze("cam1", frame)

    assert result.face_names == ["Desconocido"]
    unknown = db.get_events(event_type=EventType.FACE_UNKNOWN)
    assert len(unknown) == 1
    assert unknown[0].severity == "warning"
    assert result.snapshot_path is not None
    assert db.get_events()[0].snapshot_path == result.snapshot_path


def test_person_without_faces(tmp_path, make_settings, frame):
    class OnlyChair:
        def detect(self, _frame):
            return [Detection("chair", 0.8, (10, 10, 60, 90))]

    settings = make_settings(EVENT_COOLDOWN_SECONDS="0")
    pipeline, db, _zones, _registry = _build(
        tmp_path, settings, detector=OnlyChair(), with_faces=False
    )
    pipeline.analyze("cam1", frame)
    pipeline.analyze("cam1", frame)

    # No hay persona -> no hay evento de persona no identificada
    assert db.get_events(event_type=EventType.PERSON_UNIDENTIFIED) == []


def test_intrusion_logged_once_per_entry(tmp_path, make_settings, frame):
    settings = make_settings(INTRUSION_COOLDOWN_SECONDS="0")
    pipeline, db, zones, _registry = _build(tmp_path, settings)
    zones.add("cam1", "Entrada", SQUARE)

    first = pipeline.analyze("cam1", frame)
    second = pipeline.analyze("cam1", frame)

    assert first.intruded_zone_names == ["Entrada"]
    assert len([e for e in first.events if e.event_type == EventType.INTRUSION]) == 1
    assert not [e for e in second.events if e.event_type == EventType.INTRUSION]

    intrusions = db.get_events(event_type=EventType.INTRUSION)
    assert len(intrusions) == 1
    assert intrusions[0].zone_names == ("Entrada",)
    assert intrusions[0].severity == "warning"
    assert intrusions[0].snapshot_path

    active = pipeline.active_intrusions("cam1")
    assert active and active[0]["zone"] == "Entrada"


def test_intrusion_cooldown_blocks_reentry(tmp_path, make_settings, frame):
    settings = make_settings(INTRUSION_COOLDOWN_SECONDS="600", PRESENCE_TIMEOUT_SECONDS="0.01")
    pipeline, db, zones, _registry = _build(tmp_path, settings)
    zones.add("cam1", "Entrada", SQUARE)

    pipeline.analyze("cam1", frame)
    # Simulamos salida y reentrada inmediata en menos de un segundo
    pipeline._state("cam1").monitor._state.clear()
    pipeline.analyze("cam1", frame)

    assert len(db.get_events(event_type=EventType.INTRUSION)) == 1


def test_describe_current_mentions_intrusion(tmp_path, settings, frame):
    pipeline, _db, zones, _registry = _build(tmp_path, settings)
    zones.add("cam1", "Patio", SQUARE)
    result = pipeline.analyze("cam1", frame)
    text = pipeline.describe_current(result, ["Patio"])
    assert "Patio" in text
    assert "Intrusión" in text


def test_event_throttle():
    now = {"t": 100.0}
    throttle = EventThrottle(10.0, clock=lambda: now["t"])
    assert throttle.allow(("a",)) is True
    assert throttle.allow(("a",)) is False
    assert throttle.allow(("b",)) is True
    now["t"] += 11
    assert throttle.allow(("a",)) is True


def test_event_throttle_prunes_expired_keys():
    now = {"t": 0.0}
    throttle = EventThrottle(10.0, clock=lambda: now["t"])
    for index in range(100):
        assert throttle.allow((index,))
    assert len(throttle._last) == 100

    now["t"] = 100.0
    assert throttle.allow(("nuevo",))
    assert len(throttle._last) == 1  # las claves caducadas se podaron


def test_pipeline_serializes_model_inference(tmp_path, settings, frame):
    import threading
    import time

    class SlowDetector:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.guard = threading.Lock()

        def detect(self, _frame):
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            with self.guard:
                self.active -= 1
            return [Detection("person", 0.9, (100, 80, 200, 220))]

    detector = SlowDetector()
    pipeline, _db, _zones, _registry = _build(
        tmp_path, settings, detector=detector, with_faces=False
    )
    barrier = threading.Barrier(2)

    def analyze(camera_id: str) -> None:
        barrier.wait()
        pipeline.analyze(camera_id, frame)

    threads = [
        threading.Thread(target=analyze, args=("cam1",)),
        threading.Thread(target=analyze, args=("cam2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert detector.max_active == 1


def test_pipeline_rejects_empty_frame(tmp_path, settings):
    pipeline, _db, _zones, _registry = _build(tmp_path, settings)
    with pytest.raises(ValueError):
        pipeline.analyze("cam1", np.empty((0, 0, 3), dtype=np.uint8))


def test_no_snapshot_when_disabled(tmp_path, make_settings, frame):
    settings = make_settings(SNAPSHOT_ON_ALERT="false")
    pipeline, db, _zones, _registry = _build(tmp_path, settings, with_snapshot=False)
    pipeline.analyze("cam1", frame)
    result = pipeline.analyze("cam1", frame)
    assert result.snapshot_path is None
    assert db.get_events(event_type=EventType.FACE_UNKNOWN)[0].snapshot_path is None
