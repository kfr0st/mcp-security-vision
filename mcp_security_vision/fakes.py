"""Detectores falsos para CI y modo demo (``VISION_FAKE_MODELS=1``).

Permiten ejecutar tests de integración completos (MCP + SQLite + zonas) sin
descargar torch ni compilar dlib.
"""

from __future__ import annotations

from .detectors import Detection
from .faces import FakeFaceBackend

__all__ = ["FakeDetector", "FakeFaceBackend"]


class FakeDetector:
    """Devuelve objetos fijos y deterministas, útiles para probar reglas de zona."""

    def __init__(self, labels: tuple[str, ...] = ("person", "chair")) -> None:
        self.labels = labels

    def detect(self, frame) -> list[Detection]:
        height, width = frame.shape[:2]
        person = Detection(
            label=self.labels[0],
            confidence=0.93,
            box=(
                int(width * 0.42),
                int(height * 0.18),
                int(width * 0.58),
                int(height * 0.72),
            ),
        )
        detections = [person]
        if len(self.labels) > 1:
            detections.append(
                Detection(
                    label=self.labels[1],
                    confidence=0.61,
                    box=(
                        int(width * 0.05),
                        int(height * 0.58),
                        int(width * 0.18),
                        int(height * 0.80),
                    ),
                )
            )
        return detections
