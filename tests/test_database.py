"""Tests de la capa SQLite (historial permanente, zonas y personas)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from mcp_security_vision.database import Database, EventType


def _db(tmp_path) -> Database:
    return Database(tmp_path / "security.db")


def test_schema_and_persistence(tmp_path):
    db = _db(tmp_path)
    assert db.schema_version() == 2
    db.log_event("cam1", EventType.FACE_KNOWN, "Rostro conocido: Ana", metadata={"person": "Ana"})

    reopened = Database(tmp_path / "security.db")
    events = reopened.get_events()
    assert len(events) == 1
    assert events[0].description == "Rostro conocido: Ana"
    assert events[0].metadata["person"] == "Ana"


def test_log_event_returns_record_and_filters(tmp_path):
    db = _db(tmp_path)
    first = db.log_event("cam1", EventType.INTRUSION, "Intrusión zona A", severity="warning")
    db.log_event("cam2", EventType.FACE_UNKNOWN, "Desconocido", severity="warning")
    db.log_event("cam1", EventType.FACE_KNOWN, "Rostro conocido: Luis", metadata={"person": "Luis"})

    assert first.id is not None
    assert first.severity == "warning"

    all_events = db.get_events()
    assert len(all_events) == 3
    assert all_events[0].description.startswith("Rostro conocido")  # más reciente primero

    assert len(db.get_events(camera_id="cam1")) == 2
    assert len(db.get_events(event_type=EventType.FACE_UNKNOWN)) == 1
    assert db.count_events(camera_id="cam1") == 2
    assert len(db.get_events(limit=1)) == 1


def test_time_filters_and_purge(tmp_path):
    db = _db(tmp_path)
    old_stamp = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    db.log_event("cam1", EventType.SYSTEM, "evento viejo", created_at=old_stamp)
    db.log_event("cam1", EventType.SYSTEM, "evento reciente")

    assert len(db.get_events(hours=1)) == 1
    assert db.purge_events(older_than_days=5) == 1
    assert db.count_events() == 1
    assert db.purge_events() == 1
    assert db.count_events() == 0

    with pytest.raises(ValueError, match="negativo"):
        db.purge_events(older_than_days=-1)
    with pytest.raises(ValueError, match="finito"):
        db.purge_events(older_than_days=float("nan"))
    with pytest.raises(ValueError, match="finito"):
        db.purge_events(older_than_days=float("inf"))


def test_summarize(tmp_path):
    db = _db(tmp_path)
    db.log_event(
        "cam1", EventType.INTRUSION, "Intrusión", severity="warning", zone_names=("Patio",)
    )
    db.log_event(
        "cam1", EventType.INTRUSION, "Intrusión", severity="warning", zone_names=("Patio",)
    )
    db.log_event("cam2", EventType.FACE_KNOWN, "Rostro conocido: Ana", metadata={"person": "Ana"})
    db.log_event("cam2", EventType.FACE_KNOWN, "Rostro conocido: Ana", metadata={"person": "Ana"})
    db.log_event("cam2", EventType.CAMERA_ONLINE, "Cámara conectada")

    summary = db.summarize(hours=24)
    assert summary["total"] == 5
    assert summary["by_type"][EventType.INTRUSION] == 2
    assert summary["zones"] == {"Patio": 2}
    assert summary["known_faces"] == {"Ana": 2}
    assert summary["by_camera"] == {"cam1": 2, "cam2": 3}
    assert summary["first_event_at"] is not None

    only_cam1 = db.summarize(hours=24, camera_id="cam1")
    assert only_cam1["total"] == 2


def test_snapshot_path_update(tmp_path):
    db = _db(tmp_path)
    event = db.log_event("cam1", EventType.INTRUSION, "Intrusión", severity="warning")
    db.set_event_snapshot(event.id, "snapshots/alerta.jpg")
    stored = db.get_events()[0]
    assert stored.snapshot_path == "snapshots/alerta.jpg"
    assert "foto: snapshots/alerta.jpg" in stored.format_line()


def test_zones_crud(tmp_path):
    db = _db(tmp_path)
    zone = db.upsert_zone("cam1", "Patio", [(0.1, 0.1), (0.5, 0.1), (0.5, 0.9)], labels=["person"])
    assert zone.id is not None
    assert db.count_zones() == 1

    updated = db.upsert_zone("cam1", "Patio", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0)], enabled=False)
    assert updated.id == zone.id  # mismo registro (ON CONFLICT)
    assert updated.enabled is False
    assert db.count_zones() == 1

    assert db.get_zone("cam1", "patio") is not None  # búsqueda sin distinguir mayúsculas
    assert db.get_zones(only_enabled=True) == []
    assert db.delete_zone("cam1", "PATIO") is True
    assert db.delete_zone("cam1", "Patio") is False


def test_zone_upsert_is_case_insensitive(tmp_path):
    db = _db(tmp_path)
    first = db.upsert_zone("cam1", "Patio", [(0.1, 0.1), (0.5, 0.1), (0.5, 0.9)])
    second = db.upsert_zone("cam1", "PATIO", [(0.0, 0.0), (0.4, 0.0), (0.4, 0.8)])

    assert second.id == first.id
    assert second.name == "Patio"  # conserva el nombre original
    assert db.count_zones() == 1


def test_migration_deduplicates_zones_case_insensitively(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(path)
    with db.connect() as conn:
        # Simula una base v1: sin el índice NOCASE y con duplicados "Patio"/"patio".
        conn.execute("DROP INDEX IF EXISTS idx_zones_unique_nocase")
        conn.execute("PRAGMA user_version=1")
        for name in ("Patio", "patio"):
            conn.execute(
                """
                INSERT INTO zones (camera_id, name, points, labels, enabled, created_at, updated_at)
                VALUES ('cam1', ?, '[[0.1,0.1],[0.5,0.1],[0.5,0.9]]', '["person"]', 1, 'x', 'x')
                """,
                (name,),
            )

    migrated = Database(path)
    assert migrated.schema_version() == 2
    assert migrated.count_zones() == 1
    assert migrated.get_zone("cam1", "PATIO") is not None

    # El índice único impide volver a insertar la variante de mayúsculas.
    with pytest.raises(sqlite3.IntegrityError), migrated.connect() as conn:
        conn.execute(
            """
            INSERT INTO zones (camera_id, name, points, labels, enabled, created_at, updated_at)
            VALUES ('cam1', 'pAtIo', '[]', '[]', 1, 'x', 'x')
            """
        )


def test_people_crud(tmp_path):
    db = _db(tmp_path)
    person = db.upsert_person("Ana Torres", "ana-torres", "ana-torres.jpg")
    assert person.name == "Ana Torres"
    db.upsert_person("Luis Paz", "luis-paz", "luis-paz.jpg")
    assert [p.slug for p in db.get_people()] == ["ana-torres", "luis-paz"]

    db.upsert_person("Ana T.", "ana-torres", "ana-torres.jpg")  # actualiza
    assert [p.name for p in db.get_people() if p.slug == "ana-torres"] == ["Ana T."]

    assert db.delete_people_except(["luis-paz"]) == 1
    assert [p.slug for p in db.get_people()] == ["luis-paz"]
    assert db.delete_person("luis-paz") is True
    assert db.get_people() == []
