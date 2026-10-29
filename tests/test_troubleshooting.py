"""Tests de los diagnósticos de la guía de problemas frecuentes.

Cada test valida una afirmación de ``docs/TROUBLESHOOTING.md`` para que la
documentación no se quede desfasada respecto al código.
"""

from __future__ import annotations

import logging
import os
import socket
import time

import cv2
import numpy as np
import pytest

from mcp_security_vision.camera import (
    CameraWorker,
    apply_rtsp_transport,
    build_capture_params,
    open_capture,
)
from mcp_security_vision.config import CameraConfig, Settings
from mcp_security_vision.database import Database
from mcp_security_vision.detectors import Detection
from mcp_security_vision.errors import ConfigError
from mcp_security_vision.faces import FaceStabilizer
from mcp_security_vision.pipeline import VisionPipeline
from mcp_security_vision.server import port_in_use
from mcp_security_vision.snapshots import SnapshotStore
from mcp_security_vision.tracker import TrackedDetection
from mcp_security_vision.zones import ZoneMonitor, ZoneRecord, ZoneStore, point_in_polygon

# ---------------------------------------------------------------------------
# "La zona nunca dispara": el punto de referencia es el centro inferior (pies)
# ---------------------------------------------------------------------------
ZONA_SUELO = ((0.30, 0.60), (0.70, 0.60), (0.70, 0.95), (0.30, 0.95))


def test_zone_uses_feet_not_head():
    # Persona cuyo torso cruza la zona pero los pies quedan por encima (y=0.55)
    alta = Detection("person", 0.9, (400, 150, 600, 550))
    punto = alta.normalized_bottom_center(1000, 1000)
    assert punto == (0.5, 0.55)
    assert point_in_polygon(*punto, ZONA_SUELO) is False

    # La misma persona con los pies dentro de la zona sí dispara
    dentro = Detection("person", 0.9, (400, 300, 600, 800))
    assert point_in_polygon(*dentro.normalized_bottom_center(1000, 1000), ZONA_SUELO) is True


def test_zone_label_must_match_yolo_label():
    """'Persona' no es igual a 'person': la etiqueta debe ser la de YOLO."""
    zona = ZoneRecord(
        id=1, camera_id="cam1", name="Patio", points=ZONA_SUELO, labels=("person",), enabled=True
    )
    deteccion = Detection("person", 0.9, (400, 300, 600, 800))
    tracked = TrackedDetection(track_id=1, detection=deteccion, is_new=True)

    monitor = ZoneMonitor(clock=lambda: 0.0)
    assert monitor.check("cam1", [zona], [tracked], (1000, 1000, 3), now=0.0)  # coincide

    zona_mal = ZoneRecord(
        id=2, camera_id="cam1", name="Patio", points=ZONA_SUELO, labels=("Persona",), enabled=True
    )
    monitor2 = ZoneMonitor(clock=lambda: 0.0)
    assert monitor2.check("cam1", [zona_mal], [tracked], (1000, 1000, 3), now=0.0) == []


# ---------------------------------------------------------------------------
# "[WARN] Señal perdida en bucle": transporte RTSP y timeouts
# ---------------------------------------------------------------------------
def test_apply_rtsp_transport_sets_ffmpeg_options(monkeypatch):
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    assert apply_rtsp_transport("tcp") == "rtsp_transport;tcp"
    assert os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] == "rtsp_transport;tcp"


def test_apply_rtsp_transport_respects_manual_config(monkeypatch):
    monkeypatch.setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;udp")
    assert apply_rtsp_transport("tcp") == "rtsp_transport;udp"
    assert os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] == "rtsp_transport;udp"


@pytest.mark.parametrize("value", ["auto", "", "none", "default"])
def test_apply_rtsp_transport_noop(value, monkeypatch):
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    assert apply_rtsp_transport(value) is None


def test_build_capture_params_uses_existing_properties(settings):
    params = build_capture_params(settings)
    # Solo se añaden propiedades que existan en la build de OpenCV
    expected = 0
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        expected += 2
        assert cv2.CAP_PROP_OPEN_TIMEOUT_MSEC in params
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        expected += 2
        assert cv2.CAP_PROP_READ_TIMEOUT_MSEC in params
    assert len(params) == expected


def test_open_capture_synthetic(settings):
    cap = open_capture("synthetic", settings)
    assert cap is not None
    ok, frame = cap.read()
    assert ok and frame is not None
    cap.release()


def test_open_capture_invalid_source_returns_none_fast(make_settings):
    settings = make_settings(RECONNECT_DELAY_SECONDS="1")
    started = time.monotonic()
    cap = open_capture("rtsp://usuario:clave@127.0.0.1:1/no_existe", settings)
    assert cap is None
    assert time.monotonic() - started < 15  # no se queda colgado


# ---------------------------------------------------------------------------
# Mensajes de consola (los que documenta el README)
# ---------------------------------------------------------------------------
def test_worker_logs_offline_warning(tmp_path, make_settings, caplog):
    settings = make_settings(RECONNECT_DELAY_SECONDS="0.1", MAX_RECONNECT_DELAY_SECONDS="1")
    db = Database(settings.db_path)
    pipeline = VisionPipeline(
        settings=settings,
        db=db,
        detector=None,
        face_recognizer=None,
        zone_store=ZoneStore(db),
        snapshot_store=SnapshotStore(settings.snapshots_dir),
    )
    worker = CameraWorker(
        camera=CameraConfig(id="cam1", name="x", source="rtsp://127.0.0.1:1/x"),
        pipeline=pipeline,
        db=db,
        settings=settings,
        capture_factory=lambda *_: None,  # nunca abre
    )
    with caplog.at_level(logging.WARNING, logger="mcp_security_vision.camera"):
        worker.start()
        time.sleep(0.5)
        worker.stop()
        worker.join(timeout=2)

    mensajes = [record.getMessage() for record in caplog.records]
    assert any("SIN SEÑAL" in mensaje and "Reintentando" in mensaje for mensaje in mensajes)
    assert db.count_events() >= 1  # también queda en SQLite


def test_worker_logs_online_and_first_frame(tmp_path, make_settings, caplog):
    settings = make_settings(PROCESS_EVERY_N_FRAMES="1000")
    db = Database(settings.db_path)
    pipeline = VisionPipeline(
        settings=settings,
        db=db,
        detector=None,
        face_recognizer=None,
        zone_store=ZoneStore(db),
        snapshot_store=SnapshotStore(settings.snapshots_dir),
    )
    worker = CameraWorker(
        camera=CameraConfig(id="cam1", name="Prueba", source="synthetic"),
        pipeline=pipeline,
        db=db,
        settings=settings,
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    class OneFrame:
        def __init__(self):
            self.sent = False

        def read(self):
            if self.sent:
                return False, None
            self.sent = True
            return True, frame

        def release(self):
            return None

    worker._capture_factory = lambda *_: OneFrame()
    with caplog.at_level(logging.INFO, logger="mcp_security_vision.camera"):
        worker.start()
        time.sleep(0.5)
        worker.stop()
        worker.join(timeout=2)

    mensajes = [record.getMessage() for record in caplog.records]
    assert any("Cámara conectada" in mensaje for mensaje in mensajes)
    assert any("Primer fotograma recibido: 160x120" in mensaje for mensaje in mensajes)


def test_worker_survives_db_logging_failure(tmp_path, make_settings, caplog):
    settings = make_settings(RECONNECT_DELAY_SECONDS="0.1", MAX_RECONNECT_DELAY_SECONDS="1")
    db = Database(settings.db_path)
    pipeline = VisionPipeline(
        settings=settings,
        db=db,
        detector=None,
        face_recognizer=None,
        zone_store=ZoneStore(db),
        snapshot_store=SnapshotStore(settings.snapshots_dir),
    )

    class BrokenDB:
        def log_event(self, **_kwargs):
            raise RuntimeError("disco lleno")

    worker = CameraWorker(
        camera=CameraConfig(id="cam1", name="x", source="rtsp://127.0.0.1:1/x"),
        pipeline=pipeline,
        db=BrokenDB(),
        settings=settings,
        capture_factory=lambda *_: None,
    )
    with caplog.at_level(logging.ERROR, logger="mcp_security_vision.camera"):
        worker.start()
        time.sleep(0.4)
        assert worker.is_alive()  # el fallo de SQLite no mata el hilo
        worker.stop()
        worker.join(timeout=2)

    assert any("No se pudo registrar" in record.getMessage() for record in caplog.records)


def test_worker_backoff_grows_for_short_lived_streams(tmp_path, make_settings):
    settings = make_settings(
        PROCESS_EVERY_N_FRAMES="1000",
        RECONNECT_DELAY_SECONDS="0.1",
        MAX_RECONNECT_DELAY_SECONDS="1",
    )
    db = Database(settings.db_path)
    pipeline = VisionPipeline(
        settings=settings,
        db=db,
        detector=None,
        face_recognizer=None,
        zone_store=ZoneStore(db),
        snapshot_store=SnapshotStore(settings.snapshots_dir),
    )
    frame = np.zeros((60, 80, 3), dtype=np.uint8)

    class OneFrame:
        def __init__(self):
            self.sent = False

        def read(self):
            if self.sent:
                return False, None
            self.sent = True
            return True, frame

        def release(self):
            return None

    now = {"t": 0.0}
    worker = CameraWorker(
        camera=CameraConfig(id="cam1", name="x", source="synthetic"),
        pipeline=pipeline,
        db=db,
        settings=settings,
        capture_factory=lambda *_: OneFrame(),
        clock=lambda: now["t"],
    )
    delays: list[float] = []

    def fake_wait(delay: float) -> None:
        delays.append(delay)
        now["t"] += delay
        if len(delays) >= 3:
            worker.stop()

    worker._wait = fake_wait
    worker.run()

    # Un stream que abre y cae de inmediato no reinicia el backoff.
    assert delays == [0.2, 0.4, 0.8]


# ---------------------------------------------------------------------------
# "Address already in use"
# ---------------------------------------------------------------------------
def test_port_in_use_detects_occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as servidor:
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(1)
        puerto = servidor.getsockname()[1]
        assert port_in_use("127.0.0.1", puerto) is True


def test_port_in_use_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as libre:
        libre.bind(("127.0.0.1", 0))
        puerto = libre.getsockname()[1]
    assert port_in_use("127.0.0.1", puerto) is False


def test_port_in_use_any_host():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as servidor:
        servidor.bind(("0.0.0.0", 0))
        servidor.listen(1)
        puerto = servidor.getsockname()[1]
        assert port_in_use("0.0.0.0", puerto) is True


# ---------------------------------------------------------------------------
# "Falsos positivos de personas"
# ---------------------------------------------------------------------------
def test_stabilizer_discards_flapping_names(frame):
    stabilizer = FaceStabilizer(window=5, min_votes=3)
    stabilizer.update(["persona_a"])
    stabilizer.update(["persona_b"])
    assert stabilizer.update(["persona_a"]) == set()  # solo 2 votos de 5
    stabilizer.update(["persona_a"])
    assert stabilizer.update(["persona_a"]) == {"persona_a"}  # 4 votos de 5


def test_config_rejects_min_votes_greater_than_window(tmp_path):
    with pytest.raises(ConfigError):
        Settings.from_env(
            env={
                "CAMERA_SOURCE": "synthetic",
                "FACE_STABILIZER_WINDOW": "2",
                "FACE_STABILIZER_MIN_VOTES": "3",
            },
            base_dir=tmp_path,
            load_dotenv_file=False,
        )
