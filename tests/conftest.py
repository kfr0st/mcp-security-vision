"""Fixtures compartidas por los tests."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_security_vision.config import CameraConfig, Settings  # noqa: E402


@pytest.fixture
def frame() -> np.ndarray:
    """Fotograma sintético 320x240 con algo de textura."""
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:] = (30, 40, 50)
    image[100:180, 120:200] = (200, 180, 60)
    return image


@pytest.fixture
def make_settings(tmp_path: Path):
    """Fábrica de Settings aislados (rutas temporales, sin .env real)."""

    def _make(**overrides) -> Settings:
        env = {
            "CAMERA_SOURCE": "synthetic",
            "VISION_FAKE_MODELS": "1",
            "DB_PATH": str(tmp_path / "data" / "test.db"),
            "KNOWN_FACES_DIR": str(tmp_path / "known_faces"),
            "SNAPSHOTS_DIR": str(tmp_path / "snapshots"),
            "ZONES_SEED_FILE": "none",
        }
        env.update({key: str(value) for key, value in overrides.items()})
        settings = Settings.from_env(env=env, base_dir=tmp_path, load_dotenv_file=False)
        settings.ensure_directories()
        return settings

    return _make


@pytest.fixture
def settings(make_settings) -> Settings:
    return make_settings()


@pytest.fixture
def camera() -> CameraConfig:
    return CameraConfig(id="cam1", name="Cámara de prueba", source="synthetic")
