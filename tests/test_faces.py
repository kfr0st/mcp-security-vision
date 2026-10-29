"""Tests del registro facial y del reconocedor (con backend falso)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from mcp_security_vision.database import Database
from mcp_security_vision.errors import FaceRegistrationError
from mcp_security_vision.faces import (
    UNKNOWN_NAME,
    DisabledFaceBackend,
    FaceRecognitionBackend,
    FaceRecognizer,
    FaceRegistry,
    FaceStabilizer,
    FakeFaceBackend,
    display_name_from_path,
)


def _write_image(path, color=(10, 200, 30)) -> None:
    image = np.full((120, 120, 3), color, dtype=np.uint8)
    assert cv2.imwrite(str(path), image)


def test_registry_load_and_db_sync(tmp_path, frame):
    faces_dir = tmp_path / "known_faces"
    faces_dir.mkdir()
    _write_image(faces_dir / "ana_torres.jpg")
    _write_image(faces_dir / "luis-paz.png")
    (faces_dir / "notas.txt").write_text("ignorar", encoding="utf-8")

    db = Database(tmp_path / "db.sqlite")
    registry = FaceRegistry(faces_dir, FakeFaceBackend(), db=db)
    names = registry.load()
    assert names == ["Ana Torres", "Luis Paz"]
    assert [person.name for person in db.get_people()] == ["Ana Torres", "Luis Paz"]
    assert registry.errors == []


def test_registry_ignores_invalid_files(tmp_path):
    faces_dir = tmp_path / "known_faces"
    faces_dir.mkdir()
    (faces_dir / "rota.jpg").write_text("no soy una imagen", encoding="utf-8")
    registry = FaceRegistry(faces_dir, FakeFaceBackend())
    assert registry.load() == []
    assert registry.errors


def test_disabled_backend_lists_people_without_encoding(tmp_path):
    faces_dir = tmp_path / "known_faces"
    faces_dir.mkdir()
    _write_image(faces_dir / "ana_torres.jpg")
    db = Database(tmp_path / "db.sqlite")

    registry = FaceRegistry(faces_dir, DisabledFaceBackend(), db=db)
    assert registry.load() == ["Ana Torres"]
    assert [p.name for p in db.get_people()] == ["Ana Torres"]
    assert registry.errors == []


def test_uppercase_filename_slug_is_normalized(tmp_path):
    faces_dir = tmp_path / "known_faces"
    faces_dir.mkdir()
    _write_image(faces_dir / "ANA_TORRES.jpg")
    db = Database(tmp_path / "db.sqlite")

    registry = FaceRegistry(faces_dir, FakeFaceBackend(), db=db)
    assert registry.load() == ["Ana Torres"]
    assert db.get_people()[0].slug == "ana-torres"

    # La foto se elimina aunque el nombre del archivo tenga mayúsculas.
    assert registry.forget("Ana Torres") is True
    assert not (faces_dir / "ANA_TORRES.jpg").exists()


def test_register_and_forget(tmp_path, frame):
    faces_dir = tmp_path / "known_faces"
    faces_dir.mkdir()
    db = Database(tmp_path / "db.sqlite")
    registry = FaceRegistry(faces_dir, FakeFaceBackend(), db=db)
    registry.load()

    path = registry.register_from_frame("María López", frame)
    assert path.exists()
    assert path.name == "maria-lopez.jpg"
    assert registry.snapshot()[1] == ["María López"]
    assert [p.name for p in db.get_people()] == ["María López"]

    assert registry.forget("maria lopez") is True
    assert registry.snapshot()[1] == []
    assert not path.exists()
    assert db.get_people() == []
    assert registry.forget("nadie") is False


def test_register_saves_face_crop_not_full_frame(tmp_path, frame):
    registry = FaceRegistry(tmp_path / "known_faces", FakeFaceBackend())
    path = registry.register_from_frame("Ana Torres", frame)

    saved = cv2.imread(str(path))
    assert saved is not None
    # El backend falso devuelve una caja central pequeña: se guarda solo el rostro.
    assert saved.shape[0] < frame.shape[0]
    assert saved.shape[1] < frame.shape[1]


def test_register_rejects_short_or_invalid_names(tmp_path, frame):
    registry = FaceRegistry(tmp_path / "known_faces", FakeFaceBackend())
    with pytest.raises(FaceRegistrationError):
        registry.register_from_frame("A", frame)
    with pytest.raises(FaceRegistrationError):
        registry.register_from_frame("!!!", frame)


def test_register_requires_single_face(tmp_path, frame):
    class ManyFaces(FakeFaceBackend):
        def locate_and_encode(self, rgb_frame):
            return super().locate_and_encode(rgb_frame) * 2

    registry = FaceRegistry(tmp_path / "known_faces", ManyFaces())
    with pytest.raises(FaceRegistrationError, match="rostros"):
        registry.register_from_frame("Ana", frame)


def test_recognizer_matches_known_face(tmp_path, frame):
    registry = FaceRegistry(tmp_path / "known_faces", FakeFaceBackend())
    registry.load()
    registry.register_from_frame("Ana Torres", frame)

    recognizer = FaceRecognizer(registry, FakeFaceBackend(), tolerance=0.6, scale=0.5)
    detections = recognizer.detect(frame)
    assert len(detections) == 1
    assert detections[0].name == "Ana Torres"
    assert detections[0].is_known is True
    # La caja se reescala al tamaño original
    assert detections[0].box[3] > frame.shape[1] * 0.3


def test_recognizer_marks_unknown(tmp_path, frame):
    registry = FaceRegistry(tmp_path / "known_faces", FakeFaceBackend())
    registry.load()

    class OtherFace(FakeFaceBackend):
        def __init__(self):
            super().__init__(encoding=np.ones(128))

    recognizer = FaceRecognizer(registry, OtherFace(), tolerance=0.6, scale=1.0)
    assert [d.name for d in recognizer.detect(frame)] == [UNKNOWN_NAME]


def test_match_distance_threshold():
    recognizer = FaceRecognizer(
        object(), FakeFaceBackend(), tolerance=0.6
    )  # match() no usa el registro
    known = [np.zeros(128), np.ones(128) * 0.1]
    assert recognizer.match(np.zeros(128), known, ["Ana", "Luis"])[0] == "Ana"
    # Más cerca del segundo vector (y dentro de la tolerancia) -> Luis
    assert recognizer.match(np.ones(128) * 0.11, known, ["Ana", "Luis"])[0] == "Luis"
    # Muy lejos de todos -> desconocido
    assert recognizer.match(np.ones(128) * 100, known, ["Ana", "Luis"])[0] == UNKNOWN_NAME
    assert recognizer.match(np.zeros(128), [], [])[0] == UNKNOWN_NAME


def test_stabilizer_requires_votes():
    stabilizer = FaceStabilizer(window=3, min_votes=2)
    assert stabilizer.update(["Ana"]) == set()
    assert stabilizer.update(["Ana"]) == {"Ana"}
    assert stabilizer.update(["Desconocido"]) == set()  # 1 voto de 3 lecturas
    assert stabilizer.update(["Desconocido"]) == {"Desconocido"}

    reset = FaceStabilizer(window=1, min_votes=1)
    assert reset.update(["Ana"]) == {"Ana"}


def test_display_name():
    from pathlib import Path

    assert display_name_from_path(Path("carlos_gomez.jpg")) == "Carlos Gomez"
    assert display_name_from_path(Path("juan-perez.png")) == "Juan Perez"


def test_real_backend_requires_library(monkeypatch):
    """Si face_recognition no está instalado, el error debe ser claro."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("no instalado")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(FaceRegistrationError, match="face_recognition"):
        FaceRecognitionBackend()
