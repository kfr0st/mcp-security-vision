"""Guardado y retención de capturas en disco."""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np


class SnapshotStore:
    """Guarda fotogramas JPEG con nombre único (cámara + fecha + microsegundos)."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, frame: np.ndarray, camera_id: str = "cam", tag: str = "snapshot") -> Path:
        if frame is None or getattr(frame, "size", 0) == 0:
            raise ValueError("Fotograma vacío: no se puede guardar la captura")
        safe_camera = (
            "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in camera_id)[:32] or "cam"
        )
        safe_tag = (
            "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in tag)[:32] or "snapshot"
        )
        with self._lock:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = self.directory / f"{safe_tag}_{safe_camera}_{stamp}.jpg"
        if not cv2.imwrite(str(path), frame):
            raise OSError(f"No se pudo escribir la captura en {path}")
        return path

    def encode_jpeg(self, frame: np.ndarray, quality: int = 85) -> bytes:
        """Codifica el fotograma para enviarlo por MCP (contenido de imagen)."""
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            raise OSError("OpenCV no pudo codificar el fotograma en JPEG")
        return buffer.tobytes()

    def purge_older_than(self, days: float) -> int:
        """Borra capturas antiguas (retención). Devuelve cuántas eliminó."""
        days = float(days)
        if not math.isfinite(days) or days < 0:
            raise ValueError("days debe ser un número finito no negativo")
        threshold = datetime.now() - timedelta(days=days)
        removed = 0
        for path in self.directory.glob("*.jpg"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < threshold:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed
