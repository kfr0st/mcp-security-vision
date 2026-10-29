"""Dibujo de detecciones, rostros y zonas sobre el fotograma (evidencia visual)."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from datetime import datetime

import cv2
import numpy as np

from .detectors import Detection
from .faces import UNKNOWN_NAME, FaceDetection
from .zones import ZoneRecord, polygon_to_pixels

GREEN = (80, 200, 80)
CYAN = (230, 200, 60)
RED = (60, 60, 230)
AMBER = (40, 180, 240)
WHITE = (240, 240, 240)
DARK = (20, 20, 20)


def _ascii(text: str) -> str:
    """cv2.putText solo dibuja ASCII: quitamos acentos y emojis sin romper el texto."""
    normalized = unicodedata.normalize("NFKD", str(text))
    cleaned = "".join(ch for ch in normalized if not unicodedata.combining(ch) and ord(ch) < 128)
    return cleaned.strip() or "?"


def _label(
    frame: np.ndarray, text: str, origin: tuple[int, int], color: tuple[int, int, int]
) -> None:
    x, y = origin
    text = _ascii(text)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1
    (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
    top = max(0, y - text_h - baseline - 4)
    cv2.rectangle(frame, (x, top), (x + text_w + 6, y), color, thickness=-1)
    cv2.putText(
        frame,
        text,
        (x + 3, y - baseline - 1),
        font,
        scale,
        DARK,
        thickness,
        lineType=cv2.LINE_AA,
    )


def annotate_frame(
    frame: np.ndarray,
    detections: Sequence[Detection] = (),
    faces: Sequence[FaceDetection] = (),
    zones: Sequence[ZoneRecord] = (),
    intruded_zones: Sequence[str] = (),
    camera_name: str | None = None,
) -> np.ndarray:
    """Devuelve una copia anotada del fotograma (no modifica el original)."""
    canvas = frame.copy()
    height, width = canvas.shape[:2]

    # 1. Zonas restringidas (relleno translúcido)
    intruded = set(intruded_zones)
    if zones:
        overlay = canvas.copy()
        for zone in zones:
            pixels = polygon_to_pixels(zone.points, width, height)
            if len(pixels) < 3:
                continue
            points = np.array(pixels, dtype=np.int32).reshape((-1, 1, 2))
            color = RED if zone.name in intruded else AMBER
            cv2.fillPoly(overlay, [points], color)
            cv2.polylines(canvas, [points], isClosed=True, color=color, thickness=2)
            anchor = (pixels[0][0], max(14, pixels[0][1] - 6))
            _label(canvas, f"ZONA: {zone.name}", anchor, color)
        canvas = cv2.addWeighted(overlay, 0.22, canvas, 0.78, 0)

    # 2. Objetos YOLO
    for detection in detections:
        x1, y1, x2, y2 = detection.box
        cv2.rectangle(canvas, (x1, y1), (x2, y2), GREEN, 2)
        _label(canvas, f"{detection.label} {detection.confidence:.0%}", (x1, y1), GREEN)

    # 3. Rostros (caja en convención face_recognition: top, right, bottom, left)
    for face in faces:
        top, right, bottom, left = face.box
        color = CYAN if face.name != UNKNOWN_NAME else RED
        cv2.rectangle(canvas, (left, top), (right, bottom), color, 2)
        _label(canvas, face.name, (left, bottom + 16), color)

    # 4. Cabecera con cámara + fecha
    header = _ascii(f"{camera_name or 'cam'} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    cv2.rectangle(canvas, (0, 0), (width, 26), DARK, thickness=-1)
    cv2.putText(canvas, header, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)
    return canvas
