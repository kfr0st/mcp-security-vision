"""Motores de detección de objetos (YOLOv8) y utilidades de cajas."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

Box = tuple[int, int, int, int]  # (x1, y1, x2, y2) en píxeles


@dataclass(frozen=True)
class Detection:
    """Objeto detectado en un fotograma."""

    label: str
    confidence: float
    box: Box

    @property
    def width(self) -> int:
        return max(0, self.box[2] - self.box[0])

    @property
    def height(self) -> int:
        return max(0, self.box[3] - self.box[1])

    def bottom_center(self) -> tuple[float, float]:
        """Punto de apoyo (pies) del objeto, útil para reglas de zona."""
        x1, _y1, x2, y2 = self.box
        return (x1 + x2) / 2.0, float(y2)

    def normalized_bottom_center(self, width: int, height: int) -> tuple[float, float]:
        x, y = self.bottom_center()
        width = max(1, width)
        height = max(1, height)
        return max(0.0, min(1.0, x / width)), max(0.0, min(1.0, y / height))

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "confidence": round(self.confidence, 3), "box": list(self.box)}


class YoloDetector:
    """Envoltorio fino sobre ``ultralytics.YOLO`` (import perezoso)."""

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        conf: float = 0.5,
        imgsz: int = 640,
        device: str | None = None,
        model: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.device = device
        self._model = model

    @property
    def model(self) -> Any:
        if self._model is None:
            from ultralytics import YOLO  # import perezoso: pesa mucho

            self._model = YOLO(self.model_name)
        return self._model

    def detect(self, frame: np.ndarray) -> list[Detection]:
        kwargs: dict[str, Any] = {
            "conf": self.conf,
            "imgsz": self.imgsz,
            "verbose": False,
        }
        if self.device:
            kwargs["device"] = self.device
        results = self.model.predict(frame, **kwargs)
        if not results:
            return []
        result = results[0]
        names = getattr(result, "names", None) or getattr(self.model, "names", {})
        detections: list[Detection] = []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        for box in boxes:
            try:
                confidence = float(box.conf[0])
                class_id = int(box.cls[0])
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            except (AttributeError, IndexError, TypeError, ValueError):  # pragma: no cover
                continue
            label = names[class_id]
            detections.append(
                Detection(
                    label=str(label),
                    confidence=confidence,
                    box=(int(x1), int(y1), int(x2), int(y2)),
                )
            )
        return detections


def count_labels(detections: Sequence[Detection]) -> dict[str, int]:
    """{'person': 2, 'car': 1} ordenado por frecuencia."""
    counts: dict[str, int] = {}
    for detection in detections:
        counts[detection.label] = counts.get(detection.label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def format_object_summary(detections: Sequence[Detection]) -> str:
    counts = count_labels(detections)
    if not counts:
        return "sin objetos relevantes"
    return ", ".join(f"{count} {label}" for label, count in counts.items())
