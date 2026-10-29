"""Tests de integración interna (VisionSystem con cámara sintética)."""

from __future__ import annotations

import os
import time

import pytest

from mcp_security_vision.database import EventType
from mcp_security_vision.errors import (
    CameraNotReadyError,
    FaceRegistrationError,
    UnknownCameraError,
    VisionError,
)
from mcp_security_vision.faces import DisabledFaceBackend
from mcp_security_vision.system import VisionSystem

SQUARE = [[0.30, 0.20], [0.70, 0.20], [0.70, 0.95], [0.30, 0.95]]


def _wait_until(predicate, timeout: float = 15.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


@pytest.fixture
def system(make_settings):
    settings = make_settings(PROCESS_EVERY_N_FRAMES="1", EVENT_COOLDOWN_SECONDS="0")
    system = VisionSystem(settings)
    system.start()
    try:
        yield system
    finally:
        system.stop()


def _first_analysis(system: VisionSystem) -> None:
    assert _wait_until(lambda: system.resolve_camera("").last_analysis() is not None)


def test_camera_streams_and_analyzes(system):
    worker = system.resolve_camera("")
    assert _wait_until(lambda: worker.latest_frame() is not None)
    _first_analysis(system)

    status = worker.status()
    assert status.connected is True
    assert status.frames_read > 0
    assert status.source.startswith("synthetic")

    text = system.cameras_overview()
    assert "cam1" in text
    assert "conectada" in text


def test_live_status_and_capture(system):
    _first_analysis(system)
    status = system.live_status("")
    assert "CONECTADA" in status
    assert "Objetos en escena" in status

    jpeg, path = system.capture_jpeg("", annotate=True)
    assert jpeg[:2] == b"\xff\xd8"  # cabecera JPEG
    assert path is not None and path.endswith(".jpg")

    # Las capturas pedidas por el agente no se guardan salvo que se pida.
    jpeg, path = system.capture_jpeg("", save=False)
    assert jpeg[:2] == b"\xff\xd8"
    assert path is None


def test_capture_before_first_frame_raises(make_settings):
    settings = make_settings()
    system = VisionSystem(settings)  # sin start(): no hay fotogramas
    with pytest.raises(CameraNotReadyError):
        system.capture_jpeg("")


def test_unknown_camera(system):
    with pytest.raises(UnknownCameraError):
        system.resolve_camera("inexistente")


def test_zones_workflow(system):
    message = system.add_zone("cam1", "Entrada", SQUARE)
    assert "Entrada" in message

    listing = system.list_zones()
    assert "Entrada" in listing

    _first_analysis(system)
    assert _wait_until(lambda: system.db.get_events(event_type=EventType.INTRUSION), timeout=10), (
        "No se registró ninguna intrusión"
    )

    assert "Entrada" in system.active_intrusions("cam1")

    invalid = pytest.raises(VisionError, match=r"0\.\.1")
    with invalid:
        system.add_zone("cam1", "Fuera", [[0, 0], [500, 0], [500, 500]])

    removed = system.remove_zone("cam1", "Entrada")
    assert "eliminada" in removed


def test_register_face_and_status(system):
    _first_analysis(system)
    message = system.register_face("Ana Torres", "cam1")
    assert "registrado" in message
    assert system.known_face_count() == 1
    assert "Ana Torres" in system.known_people()

    assert "Desconocido" not in system.known_people()
    assert "eliminado" in system.forget_face("Ana Torres")
    assert system.known_face_count() == 0


def test_logs_report_and_cleanup(system):
    _first_analysis(system)
    system.db.log_event(
        "cam1", EventType.FACE_KNOWN, "Rostro conocido: Ana", metadata={"person": "Ana"}
    )
    logs = system.get_logs(limit=10)
    assert "EventType" not in logs
    assert "Ana" in logs

    report = system.report(hours=1)
    assert "Eventos totales" in report
    assert "face_known" in report

    message = system.cleanup(keep_days=30)
    assert "Limpieza completada" in message


@pytest.mark.parametrize("keep_days", [0, -1, float("nan"), float("inf")])
def test_cleanup_rejects_invalid_keep_days(system, keep_days):
    with pytest.raises(VisionError, match="keep_days"):
        system.cleanup(keep_days=keep_days)


def test_cleanup_does_not_purge_snapshots_by_default(system, frame):
    snapshot = system.snapshots.save(frame, camera_id="cam1", tag="retencion")
    old_time = time.time() - 40 * 86400
    os.utime(snapshot, (old_time, old_time))

    system.cleanup(keep_days=30)
    assert snapshot.exists()  # purge_snapshots=False por defecto

    system.cleanup(keep_days=30, purge_snapshots=True)
    assert not snapshot.exists()


def test_faces_disabled_uses_noop_backend(make_settings):
    settings = make_settings(FACES_ENABLED="false")
    system = VisionSystem(settings)
    assert isinstance(system.backend, DisabledFaceBackend)
    assert system.face_recognizer is None
    assert system.status_summary()["models"]["face_recognition"] == "disabled"
    with pytest.raises(FaceRegistrationError, match="desactivado"):
        system.register_face("Ana Torres")


def test_system_can_restart_workers(make_settings):
    settings = make_settings(PROCESS_EVERY_N_FRAMES="1")
    system = VisionSystem(settings)
    system.start()
    try:
        first = system.workers["cam1"]
        assert _wait_until(lambda: first.latest_frame() is not None)

        system.stop()
        system.start()

        second = system.workers["cam1"]
        assert second is not first
        assert _wait_until(lambda: second.latest_frame() is not None)
        assert second.is_alive()
    finally:
        system.stop()


def test_summary_payload(system):
    _first_analysis(system)
    summary = system.status_summary()
    assert summary["cameras"][0]["camera_id"] == "cam1"
    assert summary["models"]["detector"] == "fake"
    assert "recent_events" in summary
