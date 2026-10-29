"""Tests del almacén de capturas."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from mcp_security_vision.snapshots import SnapshotStore


def test_save_and_encode(tmp_path, frame):
    store = SnapshotStore(tmp_path / "snapshots")
    path = store.save(frame, camera_id="cam 1", tag="alerta")
    assert path.exists()
    assert path.suffix == ".jpg"
    assert "cam-1" in path.name  # caracteres no válidos saneados

    jpeg = store.encode_jpeg(frame)
    assert jpeg[:2] == b"\xff\xd8"


def test_save_rejects_empty_frame(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    with pytest.raises(ValueError):
        store.save(np.empty((0, 0, 3), dtype=np.uint8))


def test_purge_older_than(tmp_path, frame):
    store = SnapshotStore(tmp_path / "snapshots")
    old = store.save(frame)
    new = store.save(frame)

    old_time = time.time() - 20 * 86400
    os.utime(old, (old_time, old_time))

    assert store.purge_older_than(10) == 1
    assert not old.exists()
    assert new.exists()

    with pytest.raises(ValueError, match="negativo"):
        store.purge_older_than(-1)
    with pytest.raises(ValueError, match="finito"):
        store.purge_older_than(float("nan"))
    with pytest.raises(ValueError, match="finito"):
        store.purge_older_than(float("inf"))


def test_unique_names(tmp_path, frame):
    store = SnapshotStore(tmp_path / "snapshots")
    paths = {store.save(frame, camera_id="cam1") for _ in range(5)}
    assert len(paths) == 5
