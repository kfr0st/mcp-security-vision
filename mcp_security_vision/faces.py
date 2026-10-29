"""Registro de rostros conocidos y reconocimiento facial.

El backend real usa ``face_recognition`` (dlib). Para tests y el modo demo se
inyecta :class:`FakeFaceBackend`, que devuelve siempre un rostro determinista,
de modo que la CI no necesita compilar dlib ni descargar modelos.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .config import slugify
from .database import Database, PersonRecord
from .errors import FaceRegistrationError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
UNKNOWN_NAME = "Desconocido"

Box = tuple[int, int, int, int]  # (top, right, bottom, left) - convención face_recognition


@dataclass(frozen=True)
class FaceDetection:
    """Rostro detectado en un fotograma."""

    name: str
    box: Box
    distance: float | None = None

    @property
    def is_known(self) -> bool:
        return self.name != UNKNOWN_NAME


class FaceBackend(Protocol):
    """Interfaz mínima que debe cumplir un motor de reconocimiento facial."""

    def locate_and_encode(self, rgb_frame: np.ndarray) -> list[tuple[np.ndarray, Box]]: ...

    def encoding_from_file(self, path: Path) -> np.ndarray | None: ...


class FaceRecognitionBackend:
    """Backend real basado en ``face_recognition`` (import perezoso)."""

    def __init__(self, model: str = "hog") -> None:
        self.model = model
        try:  # falla pronto y con un mensaje claro
            import face_recognition  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise FaceRegistrationError(
                "face_recognition no está instalado. Instala dlib + face-recognition "
                "o usa VISION_FAKE_MODELS=1 para el modo demo."
            ) from exc

    def locate_and_encode(self, rgb_frame: np.ndarray) -> list[tuple[np.ndarray, Box]]:
        import face_recognition

        locations = face_recognition.face_locations(rgb_frame, model=self.model)
        encodings = face_recognition.face_encodings(rgb_frame, locations)
        return [
            (np.asarray(enc, dtype=np.float64), tuple(loc))
            for enc, loc in zip(encodings, locations, strict=False)
        ]

    def encoding_from_file(self, path: Path) -> np.ndarray | None:
        import face_recognition

        image = face_recognition.load_image_file(str(path))
        encodings = face_recognition.face_encodings(image)
        if not encodings:
            return None
        return np.asarray(encodings[0], dtype=np.float64)


class DisabledFaceBackend:
    """Backend no-op cuando ``FACES_ENABLED=false``.

    Permite cargar el registro (nombres/fotos) sin importar dlib ni ejecutar
    reconocimiento. ``locate_and_encode`` no encuentra rostros, así que registrar
    una cara desde la cámara queda bloqueado en la fachada del sistema.
    """

    def locate_and_encode(self, rgb_frame: np.ndarray) -> list[tuple[np.ndarray, Box]]:
        return []

    def encoding_from_file(self, path: Path) -> np.ndarray | None:
        # Encoding de ceros solo para que el registro pueda listar las fotos;
        # nunca se usa porque VisionSystem no crea FaceRecognizer en este modo.
        return np.zeros(128, dtype=np.float64)


class FakeFaceBackend:
    """Backend determinista para tests, CI y demos sin dlib.

    Devuelve un único rostro (encoding de ceros) en la zona superior central.
    """

    def __init__(self, encoding: np.ndarray | None = None) -> None:
        self._encoding = (
            np.zeros(128, dtype=np.float64)
            if encoding is None
            else np.asarray(encoding, dtype=np.float64)
        )

    def locate_and_encode(self, rgb_frame: np.ndarray) -> list[tuple[np.ndarray, Box]]:
        height, width = rgb_frame.shape[:2]
        top = int(height * 0.10)
        bottom = int(height * 0.30)
        left = int(width * 0.40)
        right = int(width * 0.60)
        return [(self._encoding.copy(), (top, right, bottom, left))]

    def encoding_from_file(self, path: Path) -> np.ndarray | None:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError("archivo de imagen ilegible o corrupto")
        return self._encoding.copy()


def display_name_from_path(path: Path) -> str:
    """'carlos_gomez.jpg' -> 'Carlos Gomez'."""
    stem = path.stem.replace("-", "_")
    return " ".join(part.capitalize() for part in stem.split("_") if part)


class FaceRegistry:
    """Directorio ``known_faces/`` + tabla ``people`` como fuente de verdad."""

    def __init__(
        self,
        directory: str | Path,
        backend: FaceBackend,
        db: Database | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self._db = db
        self._lock = threading.RLock()
        self._encodings: list[np.ndarray] = []
        self._names: list[str] = []
        self._errors: list[str] = []

    # ------------------------------------------------------------------
    @property
    def errors(self) -> list[str]:
        with self._lock:
            return list(self._errors)

    def load(self) -> list[str]:
        """(Re)carga todas las fotos de ``known_faces/`` en memoria."""
        encodings: list[np.ndarray] = []
        names: list[str] = []
        errors: list[str] = []
        for path in sorted(self.directory.iterdir()):
            if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
                continue
            try:
                encoding = self.backend.encoding_from_file(path)
            except Exception as exc:  # pragma: no cover - backend real
                errors.append(f"{path.name}: {exc}")
                continue
            if encoding is None:
                errors.append(f"{path.name}: no se detectó ningún rostro")
                continue
            encodings.append(np.asarray(encoding, dtype=np.float64))
            names.append(display_name_from_path(path))

        with self._lock:
            self._encodings = encodings
            self._names = names
            self._errors = errors

        if self._db is not None:
            self._sync_db()
        return names

    def _sync_db(self) -> None:
        if self._db is None:
            return
        slugs: list[str] = []
        for path in sorted(self.directory.iterdir()):
            if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
                continue
            slug = slugify(path.stem)
            if not slug:
                continue
            slugs.append(slug)
            self._db.upsert_person(display_name_from_path(path), slug, path.name)
        self._db.delete_people_except(slugs)

    def snapshot(self) -> tuple[list[np.ndarray], list[str]]:
        """Copia segura (para no retener el lock durante la comparación)."""
        with self._lock:
            return list(self._encodings), list(self._names)

    # ------------------------------------------------------------------
    def register_from_frame(self, person_name: str, frame_bgr: np.ndarray) -> Path:
        """Guarda la foto del rostro actual y lo añade al registro."""
        name = person_name.strip()
        if len(name) < 2:
            raise FaceRegistrationError("El nombre debe tener al menos 2 caracteres")
        slug = slugify(name)
        if not slug:
            raise FaceRegistrationError(f"Nombre no válido: {person_name!r}")

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        found = self.backend.locate_and_encode(rgb)
        if not found:
            raise FaceRegistrationError(
                "No se detectó ningún rostro. Pide a la persona que mire a la cámara y que haya buena luz."
            )
        if len(found) > 1:
            raise FaceRegistrationError(
                f"Hay {len(found)} rostros en la imagen. Debe haber una sola persona para registrarla."
            )

        encoding = np.asarray(found[0][0], dtype=np.float64)
        path = self.directory / f"{slug}.jpg"

        # Guardar solo el rostro (con margen), no el fotograma completo: menos
        # datos biométricos del entorno y menos espacio en disco.
        top, right, bottom, left = (int(value) for value in found[0][1])
        height, width = frame_bgr.shape[:2]
        margin_x = max(1, int((right - left) * 0.20))
        margin_y = max(1, int((bottom - top) * 0.20))
        x1 = max(0, left - margin_x)
        y1 = max(0, top - margin_y)
        x2 = min(width, right + margin_x)
        y2 = min(height, bottom + margin_y)
        face_crop = frame_bgr[y1:y2, x1:x2]
        if face_crop.size == 0:  # caja inválida: no perder el registro
            face_crop = frame_bgr

        if not cv2.imwrite(str(path), face_crop):
            raise FaceRegistrationError(f"No se pudo guardar la imagen en {path}")

        with self._lock:
            replaced = False
            for index, existing in enumerate(self._names):
                if slugify(existing) == slug:
                    self._encodings[index] = encoding
                    replaced = True
                    break
            if not replaced:
                self._encodings.append(encoding)
                self._names.append(name)
        if self._db is not None:
            self._db.upsert_person(name, slug, path.name)
        return path

    def forget(self, person_name: str) -> bool:
        """Elimina una persona conocida (foto + memoria + base de datos)."""
        target_slug = slugify(person_name)
        target_name = person_name.strip().lower()
        removed = False
        with self._lock:
            keep_encodings: list[np.ndarray] = []
            keep_names: list[str] = []
            for encoding, name in zip(self._encodings, self._names, strict=False):
                if slugify(name) == target_slug or name.lower() == target_name:
                    removed = True
                    continue
                keep_encodings.append(encoding)
                keep_names.append(name)
            self._encodings = keep_encodings
            self._names = keep_names

        for path in self.directory.iterdir():
            if path.is_file() and slugify(path.stem) == target_slug:
                path.unlink(missing_ok=True)
                removed = True
        if self._db is not None:
            removed = self._db.delete_person(target_slug) or removed
        return removed

    def list_people(self) -> list[dict[str, object]]:
        with self._lock:
            memory_names = {slugify(name) for name in self._names}
        if self._db is not None:
            records: list[PersonRecord] = self._db.get_people()
            return [
                {**record.to_dict(), "in_memory": record.slug in memory_names} for record in records
            ]
        return [
            {
                "name": display_name_from_path(path),
                "slug": slugify(path.stem),
                "file_name": path.name,
                "in_memory": True,
            }
            for path in sorted(self.directory.iterdir())
            if path.suffix.lower() in IMAGE_EXTENSIONS
        ]


@dataclass
class FaceStabilizer:
    """Exige que un nombre aparezca N veces en las últimas M lecturas.

    Evita parpadeos del tipo "Ana / Desconocido / Ana" cuando hay movimiento.
    """

    window: int = 3
    min_votes: int = 2

    def __post_init__(self) -> None:
        self.window = max(1, int(self.window))
        self.min_votes = max(1, min(int(self.min_votes), self.window))
        self._history: list[set[str]] = []

    def update(self, names: Sequence[str]) -> set[str]:
        current = set(names)
        self._history.append(current)
        if len(self._history) > self.window:
            self._history = self._history[-self.window :]
        counts: dict[str, int] = {}
        for frame_names in self._history:
            for name in frame_names:
                counts[name] = counts.get(name, 0) + 1
        return {name for name in current if counts.get(name, 0) >= self.min_votes}

    def reset(self) -> None:
        self._history.clear()


class FaceRecognizer:
    """Detecta rostros en un fotograma y los compara con el registro."""

    def __init__(
        self,
        registry: FaceRegistry,
        backend: FaceBackend,
        tolerance: float = 0.6,
        scale: float = 0.25,
    ) -> None:
        self.registry = registry
        self.backend = backend
        self.tolerance = float(tolerance)
        self.scale = max(0.05, min(float(scale), 1.0))

    def detect(self, frame_bgr: np.ndarray) -> list[FaceDetection]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        if self.scale != 1.0:
            small = cv2.resize(frame_bgr, (0, 0), fx=self.scale, fy=self.scale)
        else:
            small = frame_bgr
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        found = self.backend.locate_and_encode(rgb)
        if not found:
            return []

        known_encodings, known_names = self.registry.snapshot()
        factor = 1.0 / self.scale
        detections: list[FaceDetection] = []
        for encoding, (top, right, bottom, left) in found:
            name, distance = self.match(
                np.asarray(encoding, dtype=np.float64), known_encodings, known_names
            )
            box = (
                int(top * factor),
                int(right * factor),
                int(bottom * factor),
                int(left * factor),
            )
            detections.append(FaceDetection(name=name, box=box, distance=distance))
        return detections

    def match(
        self,
        encoding: np.ndarray,
        known_encodings: Sequence[np.ndarray],
        known_names: Sequence[str],
    ) -> tuple[str, float | None]:
        if not known_encodings:
            return UNKNOWN_NAME, None
        distances = np.linalg.norm(np.asarray(known_encodings, dtype=np.float64) - encoding, axis=1)
        best = int(np.argmin(distances))
        best_distance = float(distances[best])
        if best_distance <= self.tolerance:
            return str(known_names[best]), best_distance
        return UNKNOWN_NAME, best_distance
