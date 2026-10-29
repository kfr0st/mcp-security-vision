"""Zonas restringidas: polígonos de intrusión.

Los polígonos se guardan **normalizados** (0..1) respecto al tamaño del
fotograma, así funcionan igual con una cámara 720p o 4K y con streams cuyas
dimensiones cambian tras una reconexión.

Formato de ``config/zones.example.json``::

    {
      "zones": [
        {
          "camera_id": "cam1",
          "name": "Puerta principal",
          "points": [[0.35, 0.30], [0.65, 0.30], [0.65, 0.95], [0.35, 0.95]],
          "labels": ["person"],
          "enabled": true
        }
      ]
    }
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import Database, ZoneRecord
from .tracker import TrackedDetection

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# Geometría
# ---------------------------------------------------------------------------
def validate_polygon(points: Iterable[Sequence[float]]) -> tuple[Point, ...]:
    """Valida y normaliza un polígono (>= 3 puntos, coordenadas 0..1, área > 0)."""
    cleaned: list[Point] = []
    for point in points:
        if len(point) != 2:
            raise ValueError(f"Cada punto debe tener 2 coordenadas (x, y): {point!r}")
        x, y = float(point[0]), float(point[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("Las coordenadas deben ser números finitos")
        if not (-1e-6 <= x <= 1 + 1e-6 and -1e-6 <= y <= 1 + 1e-6):
            raise ValueError(
                "Las coordenadas deben estar normalizadas en el rango 0..1 "
                f"(recibido: [{x}, {y}]). Para usar píxeles, divide por el ancho/alto de la imagen."
            )
        cleaned.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y))))
    if len(cleaned) < 3:
        raise ValueError("Un polígono necesita al menos 3 puntos")
    if polygon_area(cleaned) <= 1e-6:
        raise ValueError("El polígono tiene área cero: revisa el orden de los puntos")
    return tuple(cleaned)


def polygon_area(points: Sequence[Point]) -> float:
    """Área por fórmula del cordón (shoelace)."""
    area = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def point_in_polygon(x: float, y: float, polygon: Sequence[Point]) -> bool:
    """Ray casting: ¿está el punto (x, y) dentro del polígono?"""
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def polygon_to_pixels(points: Sequence[Point], width: int, height: int) -> list[tuple[int, int]]:
    """Convierte un polígono normalizado a coordenadas de píxel (para dibujar)."""
    return [(round(x * width), round(y * height)) for x, y in points]


# ---------------------------------------------------------------------------
# Almacén de zonas (SQLite + seed JSON)
# ---------------------------------------------------------------------------
def _seed_labels(value: Any) -> tuple[str, ...]:
    """Normaliza ``labels`` del JSON: un string no debe iterarse por caracteres."""
    if value is None:
        return ("person",)
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    cleaned = tuple(item.strip() for item in values if item.strip())
    return cleaned or ("person",)


def _seed_enabled(value: Any) -> bool:
    """Interpreta ``enabled`` del JSON; ``"false"`` debe ser False, no bool("false")."""
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "n", "off"}
    return bool(value)


class ZoneStore:
    """CRUD de zonas persistidas en SQLite."""

    def __init__(self, db: Database, seed_file: str | Path | None = None) -> None:
        self.db = db
        self.seed_file = Path(seed_file) if seed_file else None

    def seed(self) -> int:
        """Importa ``zones.example.json`` sin sobrescribir zonas ya editadas."""
        if not self.seed_file or not self.seed_file.is_file():
            return 0
        try:
            data = json.loads(self.seed_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        entries: list[Any]
        if isinstance(data, dict):
            entries = data.get("zones", [])
        elif isinstance(data, list):
            entries = data
        else:
            return 0

        imported = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            camera_id = str(entry.get("camera_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not camera_id or not name:
                continue
            if self.db.get_zone(camera_id, name) is not None:
                continue
            try:
                points = validate_polygon(entry.get("points") or [])
            except (TypeError, ValueError):
                continue
            labels = _seed_labels(entry.get("labels", ["person"]))
            self.db.upsert_zone(
                camera_id=camera_id,
                name=name,
                points=points,
                labels=labels,
                enabled=_seed_enabled(entry.get("enabled", True)),
            )
            imported += 1
        return imported

    def add(
        self,
        camera_id: str,
        name: str,
        points: Iterable[Sequence[float]],
        labels: Sequence[str] = ("person",),
        enabled: bool = True,
    ) -> ZoneRecord:
        polygon = validate_polygon(points)
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("La zona necesita un nombre")
        return self.db.upsert_zone(
            camera_id=camera_id,
            name=clean_name,
            points=polygon,
            labels=tuple(labels) or ("person",),
            enabled=enabled,
        )

    def remove(self, camera_id: str, name: str) -> bool:
        return self.db.delete_zone(camera_id, name)

    def list(self, camera_id: str | None = None, only_enabled: bool = False) -> list[ZoneRecord]:
        return self.db.get_zones(camera_id=camera_id, only_enabled=only_enabled)

    def for_camera(self, camera_id: str) -> list[ZoneRecord]:
        return self.db.get_zones(camera_id=camera_id, only_enabled=True)


# ---------------------------------------------------------------------------
# Monitor de intrusiones
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Intrusion:
    """Objeto detectado dentro de una zona restringida."""

    camera_id: str
    zone_name: str
    track_id: int | None
    label: str
    point: Point
    dwell_seconds: float
    is_new_entry: bool


class ZoneMonitor:
    """Detecta entradas a zonas (con tiempo de permanencia) por cámara.

    Mantiene estado por ``(cámara, zona, track)``: solo reporta ``is_new_entry``
    al entrar, y luego actualiza el dwell mientras el objeto siga dentro.
    """

    def __init__(
        self,
        presence_timeout: float = 2.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.presence_timeout = float(presence_timeout)
        self._clock = clock
        self._state: dict[tuple[str, str, int | None], tuple[float, float]] = {}

    def check(
        self,
        camera_id: str,
        zones: Sequence[ZoneRecord],
        tracked: Sequence[TrackedDetection],
        frame_shape: tuple[int, ...],
        now: float | None = None,
    ) -> list[Intrusion]:
        if not zones or not tracked:
            self._expire(camera_id, set(), now)
            return []

        now = self._clock() if now is None else now
        height, width = int(frame_shape[0]), int(frame_shape[1])
        seen: set[tuple[str, str, int | None]] = set()
        intrusions: list[Intrusion] = []

        for item in tracked:
            detection = item.detection
            point = detection.normalized_bottom_center(width, height)
            for zone in zones:
                if zone.labels and detection.label not in zone.labels:
                    continue
                if not point_in_polygon(point[0], point[1], zone.points):
                    continue
                key = (camera_id, zone.name, item.track_id)
                seen.add(key)
                entry = self._state.get(key)
                if entry is None:
                    self._state[key] = (now, now)
                    is_new = True
                    dwell = 0.0
                else:
                    is_new = False
                    dwell = now - entry[0]
                    self._state[key] = (entry[0], now)
                intrusions.append(
                    Intrusion(
                        camera_id=camera_id,
                        zone_name=zone.name,
                        track_id=item.track_id,
                        label=detection.label,
                        point=point,
                        dwell_seconds=dwell,
                        is_new_entry=is_new,
                    )
                )

        self._expire(camera_id, seen, now)
        return intrusions

    def _expire(
        self, camera_id: str, seen: set[tuple[str, str, int | None]], now: float | None
    ) -> None:
        now = self._clock() if now is None else now
        stale = [
            key
            for key, (_, last_seen) in self._state.items()
            if key[0] == camera_id and key not in seen and now - last_seen > self.presence_timeout
        ]
        for key in stale:
            del self._state[key]

    def active(self, camera_id: str | None = None) -> list[dict[str, Any]]:
        now = self._clock()
        stale = [
            key
            for key, (_, last_seen) in self._state.items()
            if (not camera_id or key[0] == camera_id) and now - last_seen > self.presence_timeout
        ]
        for key in stale:
            del self._state[key]

        items = []
        for (cam, zone_name, track_id), (since, last_seen) in self._state.items():
            if camera_id and cam != camera_id:
                continue
            if now - last_seen > self.presence_timeout:
                continue
            items.append(
                {
                    "camera_id": cam,
                    "zone": zone_name,
                    "track_id": track_id,
                    "dwell_seconds": round(now - since, 1),
                }
            )
        return items

    def reset(self) -> None:
        self._state.clear()


def describe_intrusion(intrusion: Intrusion) -> str:
    label = "persona" if intrusion.label == "person" else intrusion.label
    dwell = (
        f" (lleva {intrusion.dwell_seconds:.0f}s dentro)" if intrusion.dwell_seconds >= 1 else ""
    )
    return f"Intrusión: {label} en zona restringida '{intrusion.zone_name}'{dwell}"
