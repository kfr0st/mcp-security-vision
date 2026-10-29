"""Tests del dibujo de anotaciones (evidencia visual)."""

from __future__ import annotations

import numpy as np

from mcp_security_vision.detectors import Detection
from mcp_security_vision.faces import FaceDetection
from mcp_security_vision.overlay import _ascii, annotate_frame
from mcp_security_vision.zones import ZoneRecord


def test_annotate_returns_copy_and_draws(frame):
    original = frame.copy()
    zones = [
        ZoneRecord(
            id=1,
            camera_id="cam1",
            name="Puerta principal",
            points=((0.2, 0.2), (0.8, 0.2), (0.8, 0.9), (0.2, 0.9)),
            labels=("person",),
            enabled=True,
        )
    ]
    result = annotate_frame(
        frame,
        detections=[Detection("person", 0.9, (100, 40, 200, 180))],
        faces=[FaceDetection("Ana", (10, 60, 40, 20))],
        zones=zones,
        intruded_zones=["Puerta principal"],
        camera_name="Cámara sintética",
    )
    assert result.shape == frame.shape
    assert np.array_equal(original, frame)  # no modifica el original
    assert not np.array_equal(result, frame)  # sí dibuja algo


def test_ascii_strips_accents_and_emoji():
    assert _ascii("Cámara sintética") == "Camara sintetica"
    assert _ascii("ZONA: Niño") == "ZONA: Nino"
    assert _ascii("🚨 alerta") == "alerta"
    assert _ascii("") == "?"


def test_annotate_without_elements(frame):
    result = annotate_frame(frame)
    assert result.shape == frame.shape
