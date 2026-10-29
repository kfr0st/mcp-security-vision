"""Tests de geometría de zonas y del monitor de intrusiones."""

from __future__ import annotations

import json

import pytest

from mcp_security_vision.database import Database, ZoneRecord
from mcp_security_vision.detectors import Detection
from mcp_security_vision.tracker import TrackedDetection
from mcp_security_vision.zones import (
    ZoneMonitor,
    ZoneStore,
    point_in_polygon,
    polygon_area,
    polygon_to_pixels,
    validate_polygon,
)

SQUARE = ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))


def test_point_in_polygon():
    assert point_in_polygon(0.5, 0.5, SQUARE) is True
    assert point_in_polygon(0.1, 0.5, SQUARE) is False
    assert point_in_polygon(0.5, 0.9, SQUARE) is False


def test_point_in_polygon_triangle():
    triangle = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    assert point_in_polygon(0.1, 0.1, triangle)
    assert not point_in_polygon(0.9, 0.9, triangle)


def test_validate_polygon_ok():
    polygon = validate_polygon([[0, 0], [1, 0], [1, 1]])
    assert len(polygon) == 3
    assert polygon_area(polygon) == pytest.approx(0.5)


@pytest.mark.parametrize(
    "points",
    [
        [[0, 0], [1, 1]],
        [[0, 0], [1, 0], [2, 1]],
        [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
        [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
    ],
)
def test_validate_polygon_errors(points):
    with pytest.raises(ValueError):
        validate_polygon(points)


def test_polygon_to_pixels():
    assert polygon_to_pixels(SQUARE, 100, 200)[0] == (25, 50)


def _zone(name: str = "Patio", labels=("person",)) -> ZoneRecord:
    return ZoneRecord(id=1, camera_id="cam1", name=name, points=SQUARE, labels=labels)


def _tracked(point_x: float, point_y: float, track_id: int = 1, label: str = "person"):
    """Caja cuyo centro inferior cae en (point_x, point_y) normalizado."""
    width, height = 100, 100
    x = point_x * width
    y = point_y * height
    detection = Detection(label, 0.9, (int(x - 5), int(y - 20), int(x + 5), int(y)))
    return TrackedDetection(track_id=track_id, detection=detection, is_new=True)


def test_zone_store_crud_and_seed(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    seed = tmp_path / "zones.json"
    seed.write_text(
        json.dumps(
            {
                "zones": [
                    {"camera_id": "cam1", "name": "Patio", "points": [list(p) for p in SQUARE]},
                    {"camera_id": "cam1", "name": "Mala", "points": [[0.1, 0.1]]},
                ]
            }
        ),
        encoding="utf-8",
    )
    store = ZoneStore(db, seed_file=seed)
    assert store.seed() == 1  # la zona inválida se ignora
    assert store.seed() == 0  # idempotente

    store.add("cam1", "Cochera", [[0.0, 0.0], [0.2, 0.0], [0.2, 0.2]], labels=["car"])
    assert len(store.list()) == 2
    assert [z.name for z in store.for_camera("cam1")] == ["Cochera", "Patio"]
    assert store.remove("cam1", "Cochera") is True
    assert store.remove("cam1", "Cochera") is False
    with pytest.raises(ValueError):
        store.add("cam1", "Vacía", [])


def test_zone_store_seed_normalizes_string_labels_and_false_enabled(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    seed = tmp_path / "zones.json"
    seed.write_text(
        json.dumps(
            {
                "zones": [
                    {
                        "camera_id": "cam1",
                        "name": "Con etiqueta string",
                        "points": [list(p) for p in SQUARE],
                        "labels": "car",
                        "enabled": "false",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = ZoneStore(db, seed_file=seed)
    assert store.seed() == 1
    zone = store.list()[0]
    assert zone.labels == ("car",)  # no se itera carácter a carácter
    assert zone.enabled is False  # "false" no es truthy


def test_monitor_reports_entry_once_and_dwell():
    monitor = ZoneMonitor(presence_timeout=2.0, clock=lambda: 0.0)
    zones = [_zone()]
    tracked = [_tracked(0.5, 0.5)]

    first = monitor.check("cam1", zones, tracked, (100, 100, 3), now=1.0)
    assert len(first) == 1
    assert first[0].is_new_entry is True
    assert first[0].zone_name == "Patio"

    second = monitor.check("cam1", zones, tracked, (100, 100, 3), now=3.5)
    assert second[0].is_new_entry is False
    assert second[0].dwell_seconds == pytest.approx(2.5)

    active = monitor.active("cam1")
    assert active and active[0]["zone"] == "Patio"


def test_monitor_ignores_outside_and_other_labels():
    monitor = ZoneMonitor(clock=lambda: 0.0)
    outside = [_tracked(0.05, 0.05)]
    assert monitor.check("cam1", [_zone()], outside, (100, 100, 3), now=0.0) == []

    car = [_tracked(0.5, 0.5, label="car")]
    assert monitor.check("cam1", [_zone()], car, (100, 100, 3), now=0.0) == []


def test_monitor_reentry_after_timeout():
    now = {"t": 0.0}
    monitor = ZoneMonitor(presence_timeout=1.0, clock=lambda: now["t"])
    zones = [_zone()]
    tracked = [_tracked(0.5, 0.5)]

    assert monitor.check("cam1", zones, tracked, (100, 100, 3))[0].is_new_entry
    now["t"] = 5.0
    # Sin detección: se expira el estado
    assert monitor.check("cam1", zones, [], (100, 100, 3)) == []
    now["t"] = 6.0
    reentry = monitor.check("cam1", zones, tracked, (100, 100, 3))
    assert reentry[0].is_new_entry is True


def test_monitor_active_expires_stale_entries():
    now = {"t": 0.0}
    monitor = ZoneMonitor(presence_timeout=1.0, clock=lambda: now["t"])
    zones = [_zone()]
    tracked = [_tracked(0.5, 0.5)]

    monitor.check("cam1", zones, tracked, (100, 100, 3))
    assert monitor.active("cam1")

    now["t"] = 5.0
    assert monitor.active("cam1") == []
    assert monitor._state == {}  # la poda libera memoria
