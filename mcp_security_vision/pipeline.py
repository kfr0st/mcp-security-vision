"""Pipeline de análisis: detección -> tracking -> caras -> zonas -> eventos.

Es independiente de OpenCV/threads: recibe un ``frame`` y devuelve el análisis.
Así se puede testear entero con detectores falsos.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .config import Settings
from .database import Database, EventRecord, EventType
from .detectors import Detection, count_labels, format_object_summary
from .faces import UNKNOWN_NAME, FaceDetection, FaceRecognizer, FaceStabilizer
from .snapshots import SnapshotStore
from .tracker import SimpleTracker, TrackedDetection
from .zones import Intrusion, ZoneMonitor, ZoneStore, describe_intrusion

logger = logging.getLogger(__name__)


@dataclass
class CameraAnalysis:
    """Resultado del análisis de un fotograma."""

    camera_id: str
    analyzed_at: str
    detections: list[Detection] = field(default_factory=list)
    faces: list[FaceDetection] = field(default_factory=list)
    intrusions: list[Intrusion] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    snapshot_path: str | None = None

    @property
    def object_summary(self) -> str:
        return format_object_summary(self.detections)

    @property
    def face_names(self) -> list[str]:
        seen: list[str] = []
        for face in self.faces:
            if face.name not in seen:
                seen.append(face.name)
        return seen

    @property
    def intruded_zone_names(self) -> list[str]:
        return sorted({intrusion.zone_name for intrusion in self.intrusions})


class EventThrottle:
    """Evita repetir el mismo evento cada pocos segundos."""

    def __init__(
        self, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.cooldown = max(0.0, float(cooldown_seconds))
        self._clock = clock
        self._last: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: tuple, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        with self._lock:
            # Poda las claves caducadas: sin esto el diccionario crece con cada
            # track_id nuevo durante ejecuciones largas.
            cutoff = now - self.cooldown
            self._last = {item: stamp for item, stamp in self._last.items() if stamp > cutoff}
            last = self._last.get(key)
            if last is not None and (now - last) < self.cooldown:
                return False
            self._last[key] = now
            return True

    def reset(self) -> None:
        self._last.clear()


@dataclass
class _CameraState:
    tracker: SimpleTracker
    stabilizer: FaceStabilizer
    monitor: ZoneMonitor


class VisionPipeline:
    """Orquesta los modelos y decide qué se registra en el historial."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        detector,
        face_recognizer: FaceRecognizer | None,
        zone_store: ZoneStore,
        snapshot_store: SnapshotStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.db = db
        self.detector = detector
        self.face_recognizer = face_recognizer
        self.zone_store = zone_store
        self.snapshot_store = snapshot_store
        self._clock = clock
        self._event_throttle = EventThrottle(settings.event_cooldown_seconds, clock=clock)
        self._intrusion_throttle = EventThrottle(settings.intrusion_cooldown_seconds, clock=clock)
        self._states: dict[str, _CameraState] = {}
        # Los modelos (YOLO/dlib) se comparten entre cámaras. Ultralytics no
        # garantiza inferencia concurrente sobre la misma instancia; serializamos.
        self._inference_lock = threading.Lock()

    # ------------------------------------------------------------------
    def _state(self, camera_id: str) -> _CameraState:
        state = self._states.get(camera_id)
        if state is None:
            state = _CameraState(
                tracker=SimpleTracker(
                    max_age_seconds=max(2.0, self.settings.presence_timeout_seconds)
                ),
                stabilizer=FaceStabilizer(
                    window=self.settings.face_stabilizer_window,
                    min_votes=self.settings.face_stabilizer_min_votes,
                ),
                monitor=ZoneMonitor(
                    presence_timeout=self.settings.presence_timeout_seconds, clock=self._clock
                ),
            )
            self._states[camera_id] = state
        return state

    # ------------------------------------------------------------------
    def analyze(self, camera_id: str, frame: np.ndarray) -> CameraAnalysis:
        if frame is None or getattr(frame, "size", 0) == 0:
            raise ValueError("Fotograma vacío")

        state = self._state(camera_id)
        now = self._clock()
        analysis = CameraAnalysis(
            camera_id=camera_id,
            analyzed_at=datetime.now().isoformat(timespec="seconds"),
        )

        # 1. Objetos
        detections: list[Detection] = []
        if self.detector is not None:
            try:
                with self._inference_lock:
                    detections = list(self.detector.detect(frame))
            except Exception as exc:  # pragma: no cover - modelos reales
                logger.exception("Fallo del detector de objetos: %s", exc)
        analysis.detections = detections
        tracked: list[TrackedDetection] = state.tracker.update(detections, now=now)
        person_present = any(detection.label == "person" for detection in detections)

        # 2. Rostros (con estabilización temporal)
        faces: list[FaceDetection] = []
        stable_names: list[str] = []
        if self.face_recognizer is not None and (
            person_present or self.settings.faces_without_person
        ):
            try:
                with self._inference_lock:
                    raw_faces = self.face_recognizer.detect(frame)
            except Exception as exc:  # pragma: no cover - modelos reales
                logger.warning("Fallo del reconocimiento facial: %s", exc)
                raw_faces = []
            verdicts = state.stabilizer.update([face.name for face in raw_faces])
            faces = [face for face in raw_faces if face.name in verdicts]
            stable_names = list(dict.fromkeys(face.name for face in faces))
        else:
            state.stabilizer.reset()
        analysis.faces = faces

        # 3. Zonas restringidas
        zones = self.zone_store.for_camera(camera_id)
        if zones:
            analysis.intrusions = state.monitor.check(
                camera_id=camera_id,
                zones=zones,
                tracked=tracked,
                frame_shape=frame.shape,
                now=now,
            )

        # 4. Eventos
        events: list[EventRecord] = []
        alert_events: list[EventRecord] = []

        for intrusion in analysis.intrusions:
            if not intrusion.is_new_entry:
                continue
            key = ("intrusion", camera_id, intrusion.zone_name, intrusion.track_id)
            if not self._intrusion_throttle.allow(key, now=now):
                continue
            event = self.db.log_event(
                camera_id=camera_id,
                event_type=EventType.INTRUSION,
                description=describe_intrusion(intrusion),
                severity="warning",
                object_label=intrusion.label,
                track_id=intrusion.track_id,
                zone_names=(intrusion.zone_name,),
                metadata={
                    "zone": intrusion.zone_name,
                    "dwell_seconds": round(intrusion.dwell_seconds, 1),
                    "point": [round(intrusion.point[0], 4), round(intrusion.point[1], 4)],
                },
            )
            events.append(event)
            alert_events.append(event)

        for name in stable_names:
            if name == UNKNOWN_NAME:
                key = ("face_unknown", camera_id)
                if not self._event_throttle.allow(key, now=now):
                    continue
                event = self.db.log_event(
                    camera_id=camera_id,
                    event_type=EventType.FACE_UNKNOWN,
                    description="Rostro desconocido detectado",
                    severity="warning",
                    metadata={"person": UNKNOWN_NAME},
                )
                events.append(event)
                alert_events.append(event)
            else:
                key = ("face_known", camera_id, name)
                if not self._event_throttle.allow(key, now=now):
                    continue
                events.append(
                    self.db.log_event(
                        camera_id=camera_id,
                        event_type=EventType.FACE_KNOWN,
                        description=f"Rostro conocido: {name}",
                        severity="info",
                        metadata={"person": name},
                    )
                )

        if person_present and not stable_names:
            key = ("person_unidentified", camera_id)
            if self._event_throttle.allow(key, now=now):
                events.append(
                    self.db.log_event(
                        camera_id=camera_id,
                        event_type=EventType.PERSON_UNIDENTIFIED,
                        description="Persona detectada (rostro no visible o no identificado)",
                        severity="info",
                        object_label="person",
                    )
                )

        # 5. Evidencia fotográfica de las alertas
        if alert_events and self.settings.snapshot_on_alert and self.snapshot_store is not None:
            try:
                path = self.snapshot_store.save(frame, camera_id=camera_id, tag="alerta")
                analysis.snapshot_path = str(path)
                for event in alert_events:
                    if event.id is not None:
                        self.db.set_event_snapshot(event.id, str(path))
            except OSError as exc:  # pragma: no cover - disco lleno, permisos...
                logger.warning("No se pudo guardar la evidencia: %s", exc)

        analysis.events = events
        return analysis

    # ------------------------------------------------------------------
    def active_intrusions(self, camera_id: str) -> list[dict]:
        """Personas/objetos que están ahora mismo dentro de una zona restringida."""
        state = self._states.get(camera_id)
        if state is None:
            return []
        return state.monitor.active(camera_id)

    def reset_camera(self, camera_id: str) -> None:
        """Limpia el estado temporal de una cámara (al desregistrarla)."""
        self._states.pop(camera_id, None)

    def describe_current(self, analysis: CameraAnalysis, zone_names: Sequence[str] = ()) -> str:
        """Texto legible con el estado del último análisis."""
        objects = count_labels(analysis.detections)
        object_text = (
            ", ".join(f"{n} {label}" for label, n in objects.items()) or "sin objetos relevantes"
        )
        faces_text = ", ".join(analysis.face_names) or "ningún rostro identificable"
        lines = [f"- Objetos en escena: {object_text}", f"- Rostros: {faces_text}"]
        if zone_names:
            intrusions = analysis.intruded_zone_names
            if intrusions:
                lines.append(f"- ⚠️ Intrusión activa en: {', '.join(intrusions)}")
            else:
                lines.append(f"- Zonas vigiladas: {', '.join(zone_names)} (sin intrusos)")
        return "\n".join(lines)
