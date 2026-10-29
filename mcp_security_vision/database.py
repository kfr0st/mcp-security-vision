"""Persistencia en SQLite: historial de eventos, zonas y personas conocidas.

Se usa una conexión por operación (con WAL) para que varios hilos de cámara
puedan escribir sin bloquearse, y para que el archivo ``.db`` se pueda copiar
o respaldar en caliente sin corromperse.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id      TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    event_type     TEXT    NOT NULL,
    severity       TEXT    NOT NULL DEFAULT 'info',
    description    TEXT    NOT NULL,
    object_label   TEXT,
    track_id       INTEGER,
    zone_names     TEXT,
    snapshot_path  TEXT,
    metadata       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_camera     ON events(camera_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type       ON events(event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS zones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    points      TEXT NOT NULL,
    labels      TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(camera_id, name)
);
CREATE INDEX IF NOT EXISTS idx_zones_camera ON zones(camera_id);

CREATE TABLE IF NOT EXISTS people (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL UNIQUE,
    file_name    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

SEVERITIES = ("info", "warning", "critical")


class EventType:
    """Tipos de evento usados en el historial."""

    INTRUSION = "intrusion"
    FACE_KNOWN = "face_known"
    FACE_UNKNOWN = "face_unknown"
    PERSON_UNIDENTIFIED = "person_unidentified"
    OBJECT_DETECTED = "object_detected"
    CAMERA_ONLINE = "camera_online"
    CAMERA_OFFLINE = "camera_offline"
    FACE_REGISTERED = "face_registered"
    FACE_FORGOTTEN = "face_forgotten"
    ZONE_ADDED = "zone_added"
    ZONE_REMOVED = "zone_removed"
    SYSTEM = "system"

    ALL = (
        INTRUSION,
        FACE_KNOWN,
        FACE_UNKNOWN,
        PERSON_UNIDENTIFIED,
        OBJECT_DETECTED,
        CAMERA_ONLINE,
        CAMERA_OFFLINE,
        FACE_REGISTERED,
        FACE_FORGOTTEN,
        ZONE_ADDED,
        ZONE_REMOVED,
        SYSTEM,
    )


def utcnow_iso() -> str:
    """Marca de tiempo local del servidor con formato ISO-8601 (ordenable)."""
    return datetime.now().isoformat(timespec="seconds")


def _now_iso() -> str:
    return utcnow_iso()


def _as_since(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


@dataclass(frozen=True)
class EventRecord:
    id: int | None
    camera_id: str
    created_at: str
    event_type: str
    severity: str
    description: str
    object_label: str | None = None
    track_id: int | None = None
    zone_names: tuple[str, ...] = ()
    snapshot_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "created_at": self.created_at,
            "event_type": self.event_type,
            "severity": self.severity,
            "description": self.description,
            "object_label": self.object_label,
            "track_id": self.track_id,
            "zone_names": list(self.zone_names),
            "snapshot_path": self.snapshot_path,
            "metadata": self.metadata,
        }

    def format_line(self) -> str:
        stamp = self.created_at.replace("T", " ")
        zones = f" | zonas: {', '.join(self.zone_names)}" if self.zone_names else ""
        snapshot = f" | foto: {self.snapshot_path}" if self.snapshot_path else ""
        return (
            f"[{stamp}] ({self.severity.upper()}) [{self.camera_id}] "
            f"{self.event_type}: {self.description}{zones}{snapshot}"
        )


@dataclass(frozen=True)
class ZoneRecord:
    id: int | None
    camera_id: str
    name: str
    points: tuple[tuple[float, float], ...]
    labels: tuple[str, ...] = ("person",)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "name": self.name,
            "points": [list(p) for p in self.points],
            "labels": list(self.labels),
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class PersonRecord:
    id: int | None
    name: str
    slug: str
    file_name: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "file_name": self.file_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Database:
    """Acceso a la base de datos SQLite del sistema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    # ------------------------------------------------------------------
    # Infraestructura
    # ------------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            conn.executescript(_SCHEMA)
            self._migrate(conn, current)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _migrate(conn: sqlite3.Connection, current: int) -> None:
        """Migraciones incrementales del esquema."""
        if current < 2:
            # Unicidad de zonas sin distinguir mayúsculas. Si ya hubiera
            # duplicados ("Patio"/"patio"), se conserva el más antiguo.
            conn.execute(
                """
                DELETE FROM zones
                WHERE id NOT IN (
                    SELECT MIN(id) FROM zones GROUP BY camera_id, name COLLATE NOCASE
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_zones_unique_nocase "
                "ON zones(camera_id, name COLLATE NOCASE)"
            )

    def schema_version(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------
    def log_event(
        self,
        camera_id: str,
        event_type: str,
        description: str,
        severity: str = "info",
        object_label: str | None = None,
        track_id: int | None = None,
        zone_names: Sequence[str] = (),
        snapshot_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> EventRecord:
        """Inserta un evento y lo devuelve ya materializado."""
        if severity not in SEVERITIES:
            severity = "info"
        stamp = created_at or _now_iso()
        zones_json = json.dumps(list(zone_names), ensure_ascii=False) if zone_names else None
        meta_json = json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (camera_id, created_at, event_type, severity, description,
                                    object_label, track_id, zone_names, snapshot_path, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    camera_id,
                    stamp,
                    event_type,
                    severity,
                    description,
                    object_label,
                    track_id,
                    zones_json,
                    snapshot_path,
                    meta_json,
                ),
            )
            event_id = int(cursor.lastrowid or 0)
        return EventRecord(
            id=event_id,
            camera_id=camera_id,
            created_at=stamp,
            event_type=event_type,
            severity=severity,
            description=description,
            object_label=object_label,
            track_id=track_id,
            zone_names=tuple(zone_names),
            snapshot_path=snapshot_path,
            metadata=dict(metadata or {}),
        )

    def set_event_snapshot(self, event_id: int, snapshot_path: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "UPDATE events SET snapshot_path = ? WHERE id = ?", (snapshot_path, event_id)
            )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        zones = tuple(json.loads(row["zone_names"])) if row["zone_names"] else ()
        metadata = dict(json.loads(row["metadata"])) if row["metadata"] else {}
        return EventRecord(
            id=row["id"],
            camera_id=row["camera_id"],
            created_at=row["created_at"],
            event_type=row["event_type"],
            severity=row["severity"],
            description=row["description"],
            object_label=row["object_label"],
            track_id=row["track_id"],
            zone_names=zones,
            snapshot_path=row["snapshot_path"],
            metadata=metadata,
        )

    def get_events(
        self,
        limit: int = 20,
        camera_id: str | None = None,
        event_type: str | None = None,
        hours: float | None = None,
        since: datetime | str | None = None,
    ) -> list[EventRecord]:
        """Devuelve eventos, del más reciente al más antiguo."""
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        since_str = _as_since(since)
        if since_str:
            clauses.append("created_at >= ?")
            params.append(since_str)
        if hours:
            threshold = (datetime.now() - timedelta(hours=float(hours))).isoformat(
                timespec="seconds"
            )
            clauses.append("created_at >= ?")
            params.append(threshold)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        query = f"SELECT * FROM events {where} ORDER BY created_at DESC, id DESC LIMIT ?"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._event_from_row(row) for row in rows]

    def count_events(self, hours: float | None = None, camera_id: str | None = None) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if hours:
            threshold = (datetime.now() - timedelta(hours=float(hours))).isoformat(
                timespec="seconds"
            )
            clauses.append("created_at >= ?")
            params.append(threshold)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM events {where}", params).fetchone()
        return int(row[0]) if row else 0

    def summarize(self, hours: float = 24, camera_id: str | None = None) -> dict[str, Any]:
        """Resumen agregado (para reportes que consume el agente)."""
        threshold = (datetime.now() - timedelta(hours=float(hours))).isoformat(timespec="seconds")
        clauses = ["created_at >= ?"]
        params: list[Any] = [threshold]
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = int(
                conn.execute(f"SELECT COUNT(*) FROM events WHERE {where}", params).fetchone()[0]
            )
            by_type = {
                row["event_type"]: int(row["n"])
                for row in conn.execute(
                    f"SELECT event_type, COUNT(*) AS n FROM events WHERE {where} GROUP BY event_type ORDER BY n DESC",
                    params,
                )
            }
            by_camera = {
                row["camera_id"]: int(row["n"])
                for row in conn.execute(
                    f"SELECT camera_id, COUNT(*) AS n FROM events WHERE {where} GROUP BY camera_id ORDER BY n DESC",
                    params,
                )
            }
            known_faces: dict[str, int] = {}
            events = conn.execute(
                f"SELECT description, metadata FROM events WHERE {where} AND event_type = ?",
                [*params, EventType.FACE_KNOWN],
            ).fetchall()
            for row in events:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                name = meta.get("person") or row["description"]
                known_faces[str(name)] = known_faces.get(str(name), 0) + 1
            zones: dict[str, int] = {}
            intrusion_rows = conn.execute(
                f"SELECT zone_names FROM events WHERE {where} AND event_type = ?",
                [*params, EventType.INTRUSION],
            ).fetchall()
            for row in intrusion_rows:
                for zone in json.loads(row["zone_names"]) if row["zone_names"] else []:
                    zones[zone] = zones.get(zone, 0) + 1
            bounds = conn.execute(
                f"SELECT MIN(created_at) AS first, MAX(created_at) AS last FROM events WHERE {where}",
                params,
            ).fetchone()
        return {
            "hours": float(hours),
            "camera_id": camera_id,
            "since": threshold,
            "total": total,
            "by_type": by_type,
            "by_camera": by_camera,
            "known_faces": known_faces,
            "zones": zones,
            "first_event_at": bounds["first"] if bounds else None,
            "last_event_at": bounds["last"] if bounds else None,
        }

    def purge_events(self, older_than_days: float | None = None) -> int:
        """Borra eventos antiguos. Sin argumento, borra todo el historial."""
        if older_than_days is not None:
            older_than_days = float(older_than_days)
            if not math.isfinite(older_than_days) or older_than_days < 0:
                raise ValueError("older_than_days debe ser un número finito no negativo")
        with self._lock, self.connect() as conn:
            if older_than_days is None:
                cursor = conn.execute("DELETE FROM events")
            else:
                threshold = (datetime.now() - timedelta(days=float(older_than_days))).isoformat(
                    timespec="seconds"
                )
                cursor = conn.execute("DELETE FROM events WHERE created_at < ?", (threshold,))
            return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------
    # Zonas restringidas
    # ------------------------------------------------------------------
    def upsert_zone(
        self,
        camera_id: str,
        name: str,
        points: Sequence[Sequence[float]],
        labels: Sequence[str] = ("person",),
        enabled: bool = True,
    ) -> ZoneRecord:
        stamp = _now_iso()
        points_json = json.dumps([[float(x), float(y)] for x, y in points])
        labels_json = json.dumps(list(labels), ensure_ascii=False)
        with self._lock, self.connect() as conn:
            # Reutiliza la zona existente aunque cambie el uso de mayúsculas.
            existing = conn.execute(
                "SELECT name FROM zones WHERE camera_id = ? AND name = ? COLLATE NOCASE",
                (camera_id, name),
            ).fetchone()
            target_name = existing["name"] if existing else name
            values = (
                camera_id,
                target_name,
                points_json,
                labels_json,
                int(bool(enabled)),
                stamp,
                stamp,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO zones (camera_id, name, points, labels, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(camera_id, name) DO UPDATE SET
                        points = excluded.points,
                        labels = excluded.labels,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    values,
                )
            except sqlite3.IntegrityError:
                # Otra conexión creó la variante de mayúsculas entre la
                # consulta y el INSERT; actualiza esa fila.
                row = conn.execute(
                    "SELECT * FROM zones WHERE camera_id = ? AND name = ? COLLATE NOCASE",
                    (camera_id, name),
                ).fetchone()
                if row is None:
                    raise
                conn.execute(
                    "UPDATE zones SET points = ?, labels = ?, enabled = ?, updated_at = ? WHERE id = ?",
                    (points_json, labels_json, int(bool(enabled)), stamp, row["id"]),
                )
            row = conn.execute(
                "SELECT * FROM zones WHERE camera_id = ? AND name = ? COLLATE NOCASE",
                (camera_id, target_name),
            ).fetchone()
        return self._zone_from_row(row)

    @staticmethod
    def _zone_from_row(row: sqlite3.Row) -> ZoneRecord:
        points = tuple((float(x), float(y)) for x, y in json.loads(row["points"]))
        labels = tuple(json.loads(row["labels"])) if row["labels"] else ("person",)
        return ZoneRecord(
            id=row["id"],
            camera_id=row["camera_id"],
            name=row["name"],
            points=points,
            labels=labels,
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_zones(
        self, camera_id: str | None = None, only_enabled: bool = False
    ) -> list[ZoneRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if only_enabled:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM zones {where} ORDER BY camera_id, name", params
            ).fetchall()
        return [self._zone_from_row(row) for row in rows]

    def get_zone(self, camera_id: str, name: str) -> ZoneRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM zones WHERE camera_id = ? AND name = ? COLLATE NOCASE",
                (camera_id, name),
            ).fetchone()
        return self._zone_from_row(row) if row else None

    def delete_zone(self, camera_id: str, name: str) -> bool:
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM zones WHERE camera_id = ? AND name = ? COLLATE NOCASE",
                (camera_id, name),
            )
            return bool(cursor.rowcount)

    def count_zones(self, camera_id: str | None = None) -> int:
        with self.connect() as conn:
            if camera_id:
                row = conn.execute(
                    "SELECT COUNT(*) FROM zones WHERE camera_id = ?", (camera_id,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM zones").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Personas conocidas (registro facial)
    # ------------------------------------------------------------------
    def upsert_person(self, name: str, slug: str, file_name: str) -> PersonRecord:
        stamp = _now_iso()
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO people (name, slug, file_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name = excluded.name,
                    file_name = excluded.file_name,
                    updated_at = excluded.updated_at
                """,
                (name, slug, file_name, stamp, stamp),
            )
            row = conn.execute("SELECT * FROM people WHERE slug = ?", (slug,)).fetchone()
        return self._person_from_row(row)

    @staticmethod
    def _person_from_row(row: sqlite3.Row) -> PersonRecord:
        return PersonRecord(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            file_name=row["file_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_people(self) -> list[PersonRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM people ORDER BY name").fetchall()
        return [self._person_from_row(row) for row in rows]

    def delete_person(self, slug: str) -> bool:
        with self._lock, self.connect() as conn:
            cursor = conn.execute("DELETE FROM people WHERE slug = ?", (slug,))
            return bool(cursor.rowcount)

    def delete_people_except(self, slugs: Sequence[str]) -> int:
        """Sincroniza la tabla con las fotos que realmente existen en disco."""
        keep = list(slugs)
        with self._lock, self.connect() as conn:
            if keep:
                placeholders = ",".join("?" for _ in keep)
                cursor = conn.execute(
                    f"DELETE FROM people WHERE slug NOT IN ({placeholders})", keep
                )
            else:
                cursor = conn.execute("DELETE FROM people")
            return int(cursor.rowcount or 0)
