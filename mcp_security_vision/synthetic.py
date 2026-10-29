"""Cámara sintética para demos, CI y pruebas sin hardware.

``CAMERA_SOURCE=synthetic`` permite probar todo el flujo (YOLO, zonas, MCP,
SQLite, Docker) sin conectar ninguna cámara real.
"""

from __future__ import annotations

import math
import time
from datetime import datetime

import cv2
import numpy as np


class SyntheticCapture:
    """Imita la API mínima de ``cv2.VideoCapture``."""

    def __init__(
        self, width: int = 960, height: int = 540, fps: float = 15.0, loop_seconds: float = 12.0
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1.0, float(fps))
        self.loop_seconds = max(2.0, float(loop_seconds))
        self._released = False
        self._start = time.monotonic()
        self._frame_interval = 1.0 / self.fps
        self._next_frame = time.monotonic()

    # API compatible con cv2.VideoCapture -------------------------------
    def isOpened(self) -> bool:  # noqa: N802 - nombre de OpenCV
        return not self._released

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._released:
            return False, None
        now = time.monotonic()
        wait = self._next_frame - now
        if wait > 0:
            time.sleep(wait)
        self._next_frame = max(
            self._next_frame + self._frame_interval, time.monotonic() - self._frame_interval
        )

        elapsed = time.monotonic() - self._start
        phase = (elapsed % self.loop_seconds) / self.loop_seconds

        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame[:, :] = (38, 44, 52)
        # Suelo y pared de referencia
        cv2.rectangle(
            frame, (0, int(self.height * 0.68)), (self.width, self.height), (58, 62, 70), -1
        )
        cv2.line(
            frame,
            (0, int(self.height * 0.68)),
            (self.width, int(self.height * 0.68)),
            (90, 95, 105),
            2,
        )

        # "Persona" que pasea de izquierda a derecha
        x_center = 0.5 + 0.22 * math.sin(phase * 2 * math.pi)
        top = int(self.height * 0.30)
        bottom = int(self.height * 0.72)
        half_w = self.width * 0.055
        x1 = int(x_center * self.width - half_w)
        x2 = int(x_center * self.width + half_w)
        cv2.rectangle(frame, (x1, top), (x2, bottom), (120, 160, 210), -1)
        head_radius = int(self.width * 0.02)
        cv2.circle(frame, (int((x1 + x2) / 2), top - head_radius), head_radius, (150, 185, 225), -1)

        # "Caja" estática
        cv2.rectangle(
            frame,
            (int(self.width * 0.06), int(self.height * 0.55)),
            (int(self.width * 0.16), int(self.height * 0.68)),
            (110, 120, 90),
            -1,
        )

        cv2.putText(
            frame,
            f"CAMARA SINTETICA - {datetime.now().strftime('%H:%M:%S')}",
            (12, self.height - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        return True, frame

    def release(self) -> None:
        self._released = True

    def get(self, _prop: int) -> float:
        return 0.0
