"""Fachada de negocio del sistema de visión.

``server.py`` solo traduce estas operaciones a herramientas MCP; toda la lógica
(resolver cámara, formatear estados, registrar rostros, gestionar zonas) vive
aquí para poder testearla sin MCP.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import numpy as np

from .camera import CameraStatus, CameraWorker
from .config import Settings
from .database import Database, EventRecord, EventType
from .detectors import count_labels
from .errors import CameraNotReadyError, FaceRegistrationError, UnknownCameraError, VisionError
from .faces import (
    UNKNOWN_NAME,
    DisabledFaceBackend,
    FaceRecognitionBackend,
    FaceRecognizer,
    FaceRegistry,
    FakeFaceBackend,
)
from .fakes import FakeDetector
from .notifications import WebhookNotifier
from .pipeline import VisionPipeline
from .snapshots import SnapshotStore
from .zones import ZoneStore, validate_polygon

logger = logging.getLogger(__name__)

MAX_LIST_LIMIT = 200


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("T", " ")


class VisionSystem:
    """Contenedor de todos los componentes (DB, modelos, cámaras, zonas)."""

    def __init__(self, settings: Settings, notifier: WebhookNotifier | None = None) -> None:
        self.settings = settings
        settings.ensure_directories()

        self.db = Database(settings.db_path)
        self.snapshots = SnapshotStore(settings.snapshots_dir)

        if settings.faces_enabled:
            self.backend: Any = (
                FakeFaceBackend()
                if settings.fake_models
                else FaceRecognitionBackend(model=settings.face_model)
            )
        else:
            # No construir FaceRecognitionBackend: así FACES_ENABLED=false no exige dlib.
            self.backend = DisabledFaceBackend()
        self.registry = FaceRegistry(settings.known_faces_dir, self.backend, db=self.db)
        self.registry.load()

        if settings.fake_models:
            self.detector: Any = FakeDetector()
            logger.warning("VISION_FAKE_MODELS activo: se usan modelos simulados (demo/CI)")
        else:
            from .detectors import YoloDetector

            self.detector = YoloDetector(
                model_name=settings.yolo_model,
                conf=settings.yolo_conf,
                imgsz=settings.yolo_imgsz,
                device=settings.yolo_device,
            )

        self.face_recognizer = (
            FaceRecognizer(
                registry=self.registry,
                backend=self.backend,
                tolerance=settings.face_tolerance,
                scale=settings.face_scale,
            )
            if settings.faces_enabled
            else None
        )

        self.zones = ZoneStore(self.db, seed_file=settings.zones_seed_file)
        self.zones.seed()

        self.pipeline = VisionPipeline(
            settings=settings,
            db=self.db,
            detector=self.detector,
            face_recognizer=self.face_recognizer,
            zone_store=self.zones,
            snapshot_store=self.snapshots,
        )

        self._notifier_owned = notifier is None and bool(settings.alert_webhook_url)
        self.notifier = notifier if notifier is not None else self._create_notifier()
        self.workers = self._create_workers()
        self._started = False

    def _create_notifier(self) -> WebhookNotifier | None:
        if not self.settings.alert_webhook_url:
            return None
        return WebhookNotifier(
            url=self.settings.alert_webhook_url,
            min_severity=self.settings.alert_webhook_min_severity,
        )

    def _create_workers(self) -> dict[str, CameraWorker]:
        return {
            camera.id: CameraWorker(
                camera=camera,
                pipeline=self.pipeline,
                db=self.db,
                settings=self.settings,
                notifier=self.notifier,
            )
            for camera in self.settings.enabled_cameras
        }

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return

        # Los hilos de Python no se pueden reiniciar: tras stop() se recrean.
        if self._notifier_owned:
            self.notifier = self._create_notifier()
        if not self.workers or any(worker.stopped for worker in self.workers.values()):
            for worker in self.workers.values():
                worker.stop()
                worker.join(timeout=1.0)
            self.workers = self._create_workers()

        if self.notifier is not None:
            try:
                self.notifier.start()
            except RuntimeError as exc:  # notifier externo ya detenido
                logger.warning("No se pudo reiniciar el notifier externo: %s", exc)
        for worker in self.workers.values():
            worker.start()
        self._started = True
        self.db.log_event(
            camera_id="system",
            event_type=EventType.SYSTEM,
            description=(
                f"Sistema iniciado con {len(self.workers)} cámara(s): "
                + ", ".join(
                    f"{worker.camera.id} ({worker.camera.masked_source()})"
                    for worker in self.workers.values()
                )
            ),
            severity="info",
        )
        logger.info("Sistema de visión iniciado (%s cámaras)", len(self.workers))

    def stop(self, timeout: float = 3.0) -> None:
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=timeout)
        if self.notifier is not None:
            self.notifier.stop()
            if self._notifier_owned:
                self.notifier = None
        self._started = False
        logger.info("Sistema de visión detenido")

    def __enter__(self) -> VisionSystem:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Cámaras
    # ------------------------------------------------------------------
    def resolve_camera(self, camera_id: str | None = "") -> CameraWorker:
        """Devuelve el worker de ``camera_id`` o el de la cámara por defecto."""
        requested = (camera_id or "").strip()
        if not requested:
            requested = self.settings.default_camera().id
        worker = self.workers.get(requested)
        if worker is None:
            available = ", ".join(sorted(self.workers)) or "ninguna"
            raise UnknownCameraError(f"No existe la cámara '{requested}'. Disponibles: {available}")
        return worker

    def list_cameras(self) -> list[CameraStatus]:
        return [worker.status() for worker in self.workers.values()]

    def cameras_overview(self) -> str:
        if not self.workers:
            return "No hay cámaras configuradas."
        lines = [f"Cámaras configuradas ({len(self.workers)}):"]
        for status in self.list_cameras():
            state = "🟢 conectada" if status.connected else "🔴 sin señal"
            details = f"{status.fps:.1f} fps · {status.frames_analyzed} análisis"
            lines.append(f"- {status.name} (`{status.camera_id}`): {state} · {details}")
            lines.append(f"  fuente: {status.source}")
            if status.last_error:
                lines.append(f"  último error: {status.last_error}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Estado en vivo
    # ------------------------------------------------------------------
    def live_status(self, camera_id: str | None = "") -> str:
        worker = self.resolve_camera(camera_id)
        status = worker.status()
        analysis = worker.last_analysis()
        zones = self.zones.for_camera(worker.camera.id)
        zone_names = [zone.name for zone in zones]

        lines = [
            f"📷 Cámara **{status.name}** (`{status.camera_id}`) — "
            f"{'CONECTADA' if status.connected else 'SIN SEÑAL'} · {status.fps:.1f} fps",
            f"- Fuente: {status.source}",
            f"- Último fotograma: {_fmt_dt(status.last_frame_at)} · "
            f"análisis: {status.frames_analyzed}",
        ]
        if status.last_error and not status.connected:
            lines.append(f"- ⚠️ Último error: {status.last_error}")

        if analysis is None:
            lines.append(
                "- Aún no hay análisis: la cámara no ha entregado fotogramas o los modelos "
                "siguen cargando. Inténtalo de nuevo en unos segundos."
            )
        else:
            lines.append(self.pipeline.describe_current(analysis, zone_names))
            if analysis.snapshot_path:
                lines.append(f"- Evidencia de la última alerta: {analysis.snapshot_path}")
            if status.intrusions:
                for intrusion in status.intrusions:
                    lines.append(
                        f"- 🚨 Zona '{intrusion['zone']}': objeto #{intrusion['track_id']} "
                        f"dentro desde hace {intrusion['dwell_seconds']}s"
                    )

        recent = self.db.get_events(limit=3, camera_id=worker.camera.id)
        if recent:
            lines.append("Últimos eventos de esta cámara:")
            lines.extend(f"  · {event.format_line()}" for event in recent)
        return "\n".join(lines)

    def capture_jpeg(
        self, camera_id: str | None = "", annotate: bool = False, save: bool = True
    ) -> tuple[bytes, str | None]:
        """Devuelve (jpeg, ruta_guardada) del fotograma actual."""
        worker = self.resolve_camera(camera_id)
        frame = worker.annotated_frame() if annotate else worker.latest_frame()
        if frame is None:
            raise CameraNotReadyError(
                f"La cámara '{worker.camera.id}' todavía no ha entregado ningún fotograma "
                "(revisa la conexión RTSP/USB)."
            )
        jpeg = self.snapshots.encode_jpeg(frame)
        path: str | None = None
        if save:
            saved = self.snapshots.save(
                frame, camera_id=worker.camera.id, tag="captura_anotada" if annotate else "captura"
            )
            path = str(saved)
        return jpeg, path

    def snapshot_and_store(self, frame: np.ndarray, camera_id: str, tag: str = "captura") -> str:
        return str(self.snapshots.save(frame, camera_id=camera_id, tag=tag))

    def active_intrusions(self, camera_id: str | None = "") -> str:
        if camera_id:
            worker = self.resolve_camera(camera_id)
            targets = [worker.camera.id]
        else:
            targets = list(self.workers)
        rows: list[str] = []
        for cam in targets:
            for item in self.pipeline.active_intrusions(cam):
                rows.append(
                    f"- [{cam}] zona '{item['zone']}' · objeto #{item['track_id']} · "
                    f"{item['dwell_seconds']}s dentro"
                )
        if not rows:
            return "No hay intrusos dentro de zonas restringidas en este momento."
        return "Intrusiones activas:\n" + "\n".join(rows)

    # ------------------------------------------------------------------
    # Historial y reportes
    # ------------------------------------------------------------------
    def get_logs(
        self,
        limit: int = 20,
        camera_id: str | None = None,
        hours: float | None = None,
        event_type: str | None = None,
    ) -> str:
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        if camera_id:
            camera_id = self.resolve_camera(camera_id).camera.id  # valida el id
        events = self.db.get_events(
            limit=limit, camera_id=camera_id, hours=hours, event_type=event_type
        )
        if not events:
            return "No hay eventos registrados con esos filtros."
        header = f"Historial de seguridad (últimos {len(events)} eventos, del más reciente al más antiguo):"
        return header + "\n" + "\n".join(event.format_line() for event in events)

    def report(self, hours: float = 24, camera_id: str | None = None) -> str:
        if camera_id:
            camera_id = self.resolve_camera(camera_id).camera.id
        data = self.db.summarize(hours=hours, camera_id=camera_id)
        lines = [
            f"Resumen de seguridad de las últimas {data['hours']:.0f} h"
            + (f" (cámara `{camera_id}`)" if camera_id else " (todas las cámaras)"),
            f"- Eventos totales: {data['total']}",
        ]
        if data["total"]:
            lines.append("- Por tipo: " + ", ".join(f"{k}={v}" for k, v in data["by_type"].items()))
            lines.append(
                "- Por cámara: " + ", ".join(f"{k}={v}" for k, v in data["by_camera"].items())
            )
            if data["known_faces"]:
                lines.append(
                    "- Rostros conocidos: "
                    + ", ".join(f"{name}={count}" for name, count in data["known_faces"].items())
                )
            if data["zones"]:
                lines.append(
                    "- Intrusiones por zona: "
                    + ", ".join(f"{name}={count}" for name, count in data["zones"].items())
                )
            lines.append(
                f"- Primer evento: {_fmt_dt(data['first_event_at'])} · "
                f"último: {_fmt_dt(data['last_event_at'])}"
            )
        else:
            lines.append("- No hay actividad registrada en ese periodo.")
        return "\n".join(lines)

    def recent_events(self, limit: int = 10) -> list[EventRecord]:
        return self.db.get_events(limit=limit)

    # ------------------------------------------------------------------
    # Rostros
    # ------------------------------------------------------------------
    def register_face(self, person_name: str, camera_id: str | None = "") -> str:
        if not self.settings.faces_enabled:
            raise FaceRegistrationError(
                "El reconocimiento facial está desactivado (FACES_ENABLED=false)."
            )
        worker = self.resolve_camera(camera_id)
        frame = worker.latest_frame()
        if frame is None:
            raise CameraNotReadyError(
                f"La cámara '{worker.camera.id}' todavía no tiene imagen. "
                "Colócate frente a ella y vuelve a intentarlo."
            )
        path = self.registry.register_from_frame(person_name, frame)
        self.db.log_event(
            camera_id=worker.camera.id,
            event_type=EventType.FACE_REGISTERED,
            description=f"Rostro registrado: {person_name}",
            severity="info",
            metadata={"person": person_name, "file": path.name},
        )
        return (
            f"✅ Rostro de '{person_name}' registrado y guardado en {path}. "
            f"Ahora hay {len(self.registry.snapshot()[1])} persona(s) conocidas."
        )

    def forget_face(self, person_name: str) -> str:
        removed = self.registry.forget(person_name)
        if not removed:
            return f"No encontré ninguna persona registrada con el nombre '{person_name}'."
        self.db.log_event(
            camera_id="system",
            event_type=EventType.FACE_FORGOTTEN,
            description=f"Rostro eliminado del registro: {person_name}",
            severity="warning",
            metadata={"person": person_name},
        )
        return (
            f"🗑️ Rostro de '{person_name}' eliminado del registro, del disco y de la base de datos."
        )

    def known_people(self) -> str:
        people = self.registry.list_people()
        if not people:
            return (
                "No hay personas registradas todavía. Usa `register_current_face` con la persona "
                "frente a la cámara o guarda fotos en `known_faces/`."
            )
        lines = [f"Personas conocidas ({len(people)}):"]
        for person in people:
            flag = "" if person.get("in_memory", True) else "  ⚠️ foto sin rostro detectado"
            lines.append(
                f"- {person['name']} · archivo: {person['file_name']} · "
                f"alta: {_fmt_dt(str(person.get('created_at', '')))}{flag}"
            )
        errors = self.registry.errors
        if errors:
            lines.append("Avisos de carga: " + "; ".join(errors))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Zonas restringidas
    # ------------------------------------------------------------------
    def add_zone(
        self,
        camera_id: str | None,
        name: str,
        points: Iterable[Sequence[float]],
        labels: Sequence[str] = ("person",),
        enabled: bool = True,
    ) -> str:
        worker = self.resolve_camera(camera_id)
        try:
            polygon = validate_polygon(points)
        except ValueError as exc:
            raise VisionError(str(exc)) from exc
        zone = self.zones.add(
            camera_id=worker.camera.id,
            name=name,
            points=polygon,
            labels=tuple(labels) or ("person",),
            enabled=enabled,
        )
        self.db.log_event(
            camera_id=worker.camera.id,
            event_type=EventType.ZONE_ADDED,
            description=f"Zona restringida definida: {zone.name}",
            severity="info",
            zone_names=(zone.name,),
            metadata={"labels": list(zone.labels), "enabled": zone.enabled},
        )
        pretty = " ".join(f"({x:.2f},{y:.2f})" for x, y in zone.points)
        state = "activa" if zone.enabled else "deshabilitada"
        return (
            f"✅ Zona '{zone.name}' guardada en la cámara `{worker.camera.id}` ({state}). "
            f"Polígono normalizado: {pretty}. Vigilando: {', '.join(zone.labels)}. "
            "Recuerda: el punto de referencia es el centro inferior de la caja del objeto "
            "(los pies de la persona), no su cabeza."
        )

    def remove_zone(self, camera_id: str | None, name: str) -> str:
        worker = self.resolve_camera(camera_id)
        if not self.zones.remove(worker.camera.id, name):
            return f"No existe la zona '{name}' en la cámara `{worker.camera.id}`."
        self.db.log_event(
            camera_id=worker.camera.id,
            event_type=EventType.ZONE_REMOVED,
            description=f"Zona restringida eliminada: {name}",
            severity="info",
            zone_names=(name,),
        )
        return f"🗑️ Zona '{name}' eliminada de la cámara `{worker.camera.id}`."

    def list_zones(self, camera_id: str | None = None) -> str:
        zones = self.zones.list(camera_id=camera_id)
        if not zones:
            return "No hay zonas restringidas definidas."
        lines = [f"Zonas restringidas ({len(zones)}):"]
        for zone in zones:
            pretty = " ".join(f"({x:.2f},{y:.2f})" for x, y in zone.points)
            state = "activa" if zone.enabled else "deshabilitada"
            lines.append(
                f"- [{zone.camera_id}] {zone.name} · {state} · objetos: {', '.join(zone.labels)}"
            )
            lines.append(f"  polígono (normalizado): {pretty}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mantenimiento
    # ------------------------------------------------------------------
    def cleanup(self, keep_days: float = 30, purge_snapshots: bool = False) -> str:
        keep_days = float(keep_days)
        if not math.isfinite(keep_days) or keep_days < 1:
            raise VisionError("keep_days debe ser un número finito mayor o igual que 1")
        deleted_events = self.db.purge_events(older_than_days=keep_days)
        deleted_files = self.snapshots.purge_older_than(keep_days) if purge_snapshots else 0
        self.db.log_event(
            camera_id="system",
            event_type=EventType.SYSTEM,
            description=f"Limpieza: {deleted_events} eventos y {deleted_files} capturas antiguas eliminados",
            severity="info",
        )
        return (
            f"🧹 Limpieza completada: {deleted_events} eventos y {deleted_files} capturas "
            f"con más de {keep_days:.0f} días eliminados."
        )

    def status_summary(self) -> dict[str, Any]:
        """Resumen estructurado (para el recurso MCP ``security://status``)."""
        statuses = self.list_cameras()
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "cameras": [status.to_dict() for status in statuses],
            "known_people": [person["name"] for person in self.registry.list_people()],
            "zones": [zone.to_dict() for zone in self.zones.list()],
            "recent_events": [event.to_dict() for event in self.recent_events(limit=5)],
            "models": {
                "detector": "fake" if self.settings.fake_models else self.settings.yolo_model,
                "face_recognition": (
                    "disabled"
                    if not self.settings.faces_enabled
                    else ("fake" if self.settings.fake_models else "dlib/face_recognition")
                ),
                "face_tolerance": self.settings.face_tolerance,
            },
        }

    def objects_in_frame(self, camera_id: str | None = "") -> dict[str, int]:
        worker = self.resolve_camera(camera_id)
        analysis = worker.last_analysis()
        return count_labels(analysis.detections) if analysis else {}

    def known_face_count(self) -> int:
        return len(self.registry.snapshot()[1])

    def describe_unknown(self, name: str) -> str:
        return "Desconocido" if name == UNKNOWN_NAME else name
