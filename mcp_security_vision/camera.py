"""Hilo de captura por cámara con reconexión automática.

Cada cámara corre en su propio hilo ``daemon``: el bucle de vídeo nunca bloquea
las herramientas MCP, y una caída de red RTSP se reintenta con backoff
exponencial sin tirar el servidor.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import cv2
import numpy as np

from .config import CameraConfig, Settings
from .database import Database, EventType
from .notifications import WebhookNotifier
from .overlay import annotate_frame
from .pipeline import CameraAnalysis, EventThrottle, VisionPipeline
from .synthetic import SyntheticCapture

logger = logging.getLogger(__name__)


class CaptureLike(Protocol):
    """Interfaz mínima compartida por ``cv2.VideoCapture`` y la cámara sintética."""

    def read(self) -> tuple[bool, np.ndarray | None]: ...

    def release(self) -> None: ...


CaptureFactory = Callable[[Any, Settings], "CaptureLike | None"]


def build_capture_params(settings: Settings) -> list[int]:
    """Parámetros de apertura con timeout (solo las propiedades que existan).

    ``CAP_PROP_OPEN_TIMEOUT_MSEC`` no está en todas las builds de OpenCV: se
    comprueba con ``getattr`` para no romper en backends antiguos.
    """
    timeout_ms = int(max(1.0, float(settings.reconnect_delay_seconds)) * 1000)
    params: list[int] = []
    for attr in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
        prop = getattr(cv2, attr, None)
        if prop is not None:
            params.extend([int(prop), timeout_ms])
    return params


def apply_rtsp_transport(transport: str) -> str | None:
    """Fuerza el transporte RTSP de FFmpeg (tcp/udp) vía variable de entorno.

    Muchas cámaras pierden paquetes con UDP y acaban mostrando "señal perdida"
    en bucle; con TCP el stream es más estable. Devuelve las opciones aplicadas
    (``None`` si no hay nada que aplicar o si el usuario ya las definió).
    """
    value = (transport or "").strip().lower()
    if value in {"", "auto", "none", "default"}:
        return None
    option = f"rtsp_transport;{value}"
    existing = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "").strip()
    if "rtsp_transport" in existing:
        return existing  # respetamos la configuración explícita del usuario
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"{existing};{option}" if existing else option
    return option


def open_capture(source: Any, settings: Settings) -> CaptureLike | None:
    """Abre una fuente de vídeo con timeouts (clave para RTSP que no responde)."""
    if source == "synthetic":
        return SyntheticCapture()

    cap: Any
    if isinstance(source, int):
        cap = cv2.VideoCapture(source)
    else:
        applied = apply_rtsp_transport(settings.rtsp_transport)
        if applied:
            logger.debug("Opciones FFmpeg aplicadas: %s", applied)
        try:
            cap = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG, build_capture_params(settings))
        except (AttributeError, cv2.error, TypeError, ValueError):
            # Backend sin soporte de parámetros: abrimos sin timeouts explícitos
            cap = cv2.VideoCapture(str(source))

    if cap is None or not cap.isOpened():
        if cap is not None:
            cap.release()
        return None
    # baja latencia: no acumular fotogramas viejos en el buffer
    with contextlib.suppress(Exception):  # pragma: no cover - depende del backend
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


@dataclass
class CameraStatus:
    """Foto instantánea del estado de una cámara (para el agente)."""

    camera_id: str
    name: str
    source: str
    connected: bool
    frames_read: int
    frames_analyzed: int
    fps: float
    reconnects: int
    last_frame_at: str | None
    last_analysis_at: str | None
    last_error: str | None
    objects: list[str] = field(default_factory=list)
    faces: list[str] = field(default_factory=list)
    active_zones: list[str] = field(default_factory=list)
    intrusions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "source": self.source,
            "connected": self.connected,
            "frames_read": self.frames_read,
            "frames_analyzed": self.frames_analyzed,
            "fps": round(self.fps, 1),
            "reconnects": self.reconnects,
            "last_frame_at": self.last_frame_at,
            "last_analysis_at": self.last_analysis_at,
            "last_error": self.last_error,
            "objects": list(self.objects),
            "faces": list(self.faces),
            "active_zones": list(self.active_zones),
            "intrusions": list(self.intrusions),
        }


class CameraWorker(threading.Thread):
    """Hilo que lee la cámara, analiza cada N frames y guarda el último estado."""

    def __init__(
        self,
        camera: CameraConfig,
        pipeline: VisionPipeline,
        db: Database,
        settings: Settings,
        capture_factory: CaptureFactory | None = None,
        notifier: WebhookNotifier | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name=f"camera-{camera.id}", daemon=True)
        self.camera = camera
        self.pipeline = pipeline
        self.db = db
        self.settings = settings
        self.notifier = notifier
        self._capture_factory = capture_factory or open_capture
        self._clock = clock
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        self._frame: np.ndarray | None = None
        self._analysis: CameraAnalysis | None = None
        self._connected = False
        self._frames_read = 0
        self._frames_analyzed = 0
        self._reconnects = 0
        self._last_error: str | None = None
        self._last_frame_at: str | None = None
        self._last_analysis_at: str | None = None
        self._fps = 0.0
        self._fps_window_start = clock()
        self._fps_window_frames = 0
        self._was_connected = False
        self._offline_throttle = EventThrottle(
            max(60.0, settings.reconnect_delay_seconds * 6), clock=clock
        )
        self._online_throttle = EventThrottle(
            max(60.0, settings.reconnect_delay_seconds * 6), clock=clock
        )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def last_analysis(self) -> CameraAnalysis | None:
        with self._lock:
            return self._analysis

    def annotated_frame(self, zones: bool = True) -> np.ndarray | None:
        """Último fotograma con cajas, rostros y zonas dibujadas."""
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            analysis = self._analysis
        if frame is None:
            return None
        detections = analysis.detections if analysis else []
        faces = analysis.faces if analysis else []
        zone_list = self.pipeline.zone_store.for_camera(self.camera.id) if zones else []
        intruded = analysis.intruded_zone_names if analysis else []
        return annotate_frame(
            frame,
            detections=detections,
            faces=faces,
            zones=zone_list,
            intruded_zones=intruded,
            camera_name=self.camera.name,
        )

    def status(self) -> CameraStatus:
        with self._lock:
            analysis = self._analysis
            return CameraStatus(
                camera_id=self.camera.id,
                name=self.camera.name,
                source=self.camera.masked_source(),
                connected=self._connected,
                frames_read=self._frames_read,
                frames_analyzed=self._frames_analyzed,
                fps=self._fps,
                reconnects=self._reconnects,
                last_frame_at=self._last_frame_at,
                last_analysis_at=self._last_analysis_at,
                last_error=self._last_error,
                objects=[detection.label for detection in analysis.detections] if analysis else [],
                faces=list(analysis.face_names) if analysis else [],
                active_zones=[
                    zone.name for zone in self.pipeline.zone_store.for_camera(self.camera.id)
                ],
                intrusions=self.pipeline.active_intrusions(self.camera.id),
            )

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------
    def run(self) -> None:
        delay = self.settings.reconnect_delay_seconds
        while not self._stop_event.is_set():
            capture = self._open()
            if capture is None:
                self._mark_offline("no se pudo abrir la fuente de vídeo", retry_in=delay)
                self._wait(delay)
                delay = min(delay * 2, self.settings.max_reconnect_delay_seconds)
                continue

            opened_at = self._clock()
            self._mark_online()
            frame_index = 0
            try:
                while not self._stop_event.is_set():
                    try:
                        ok, frame = capture.read()
                    except Exception as exc:  # pragma: no cover - backend RTSP
                        logger.warning("[%s] Error leyendo el stream: %s", self.camera.id, exc)
                        ok, frame = False, None

                    if not ok or frame is None:
                        break

                    self._register_frame(frame)
                    frame_index += 1
                    if frame_index % self.settings.process_every_n_frames == 0:
                        self._process(frame)
            finally:
                with contextlib.suppress(Exception):  # pragma: no cover
                    capture.release()

            if self._stop_event.is_set():
                break

            # Solo se reinicia el backoff si el stream fue estable. Una cámara
            # que abre y cae de inmediato debe seguir con backoff exponencial.
            stable_for = self._clock() - opened_at
            stable_after = max(10.0, self.settings.reconnect_delay_seconds * 3)
            if stable_for >= stable_after:
                delay = self.settings.reconnect_delay_seconds
            else:
                delay = min(delay * 2, self.settings.max_reconnect_delay_seconds)
            self._mark_offline("señal de vídeo perdida o fin del stream", retry_in=delay)
            self._wait(delay)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _open(self) -> CaptureLike | None:
        try:
            return self._capture_factory(self.camera.source, self.settings)
        except Exception as exc:  # pragma: no cover - backend real
            logger.warning("[%s] Excepción abriendo la cámara: %s", self.camera.id, exc)
            return None

    def _wait(self, seconds: float) -> None:
        self._stop_event.wait(max(0.05, float(seconds)))

    def _register_frame(self, frame: np.ndarray) -> None:
        now = self._clock()
        with self._lock:
            first = self._frames_read == 0
            self._frame = frame.copy()
            self._frames_read += 1
            self._last_frame_at = datetime.now().isoformat(timespec="seconds")
            self._fps_window_frames += 1
            elapsed = now - self._fps_window_start
            if elapsed >= 1.0:
                self._fps = self._fps_window_frames / elapsed
                self._fps_window_start = now
                self._fps_window_frames = 0
        if first:
            height, width = frame.shape[:2]
            logger.info(
                "[%s] Primer fotograma recibido: %dx%d (%s)",
                self.camera.id,
                width,
                height,
                self.camera.masked_source(),
            )

    def _process(self, frame: np.ndarray) -> None:
        try:
            analysis = self.pipeline.analyze(self.camera.id, frame)
        except Exception as exc:  # pragma: no cover - errores de modelo
            logger.exception("[%s] Error analizando el fotograma: %s", self.camera.id, exc)
            return
        with self._lock:
            self._analysis = analysis
            self._frames_analyzed += 1
            self._last_analysis_at = analysis.analyzed_at
        if self.notifier is not None and analysis.events:
            self.notifier.notify_events(analysis.events)

    def _log_event_safe(self, **kwargs: Any) -> None:
        """Registra en SQLite sin tumbar el hilo de la cámara si la DB falla."""
        try:
            self.db.log_event(**kwargs)
        except Exception as exc:  # pragma: no cover - disco lleno, DB bloqueada...
            logger.error("[%s] No se pudo registrar el evento en SQLite: %s", self.camera.id, exc)

    def _mark_online(self) -> None:
        with self._lock:
            was_connected = self._was_connected
            self._connected = True
            self._was_connected = True
            if was_connected:
                self._reconnects += 1
        if self._online_throttle.allow(("camera_online", self.camera.id)):
            if was_connected:
                logger.info(
                    "[%s] Cámara RECONECTADA (%s)", self.camera.id, self.camera.masked_source()
                )
            else:
                logger.info(
                    "[%s] Cámara conectada (%s)", self.camera.id, self.camera.masked_source()
                )
            self._log_event_safe(
                camera_id=self.camera.id,
                event_type=EventType.CAMERA_ONLINE,
                description=(
                    "Cámara reconectada"
                    if was_connected
                    else f"Cámara conectada ({self.camera.masked_source()})"
                ),
                severity="info",
            )

    def _mark_offline(self, reason: str, retry_in: float | None = None) -> None:
        with self._lock:
            self._connected = False
            self._last_error = reason
        retry_text = f". Reintentando en {retry_in:g}s..." if retry_in else ""
        logger.warning(
            "[%s] Cámara SIN SEÑAL: %s%s",
            self.camera.id,
            reason,
            retry_text,
        )
        if self._offline_throttle.allow(("camera_offline", self.camera.id)):
            self._log_event_safe(
                camera_id=self.camera.id,
                event_type=EventType.CAMERA_OFFLINE,
                description=f"Cámara sin señal: {reason}",
                severity="critical",
            )
