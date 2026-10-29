"""Tests de configuración y parseo de variables de entorno."""

from __future__ import annotations

import json

import pytest

from mcp_security_vision.config import CameraConfig, Settings, mask_credentials, slugify
from mcp_security_vision.errors import ConfigError


def _settings(env: dict[str, str], tmp_path) -> Settings:
    return Settings.from_env(env=env, base_dir=tmp_path, load_dotenv_file=False)


def test_defaults_single_camera(tmp_path):
    settings = _settings({"CAMERA_SOURCE": "0"}, tmp_path)
    assert len(settings.cameras) == 1
    assert settings.cameras[0].id == "cam1"
    assert settings.cameras[0].source == 0
    assert isinstance(settings.cameras[0].source, int)
    assert settings.transport == "stdio"
    assert settings.db_path == tmp_path / "data/security.db"


def test_synthetic_and_rtsp_sources(tmp_path):
    settings = _settings(
        {
            "CAMERAS": json.dumps(
                {"demo": "synthetic", "entrada": "rtsp://admin:secreto@10.0.0.5:554/stream1"}
            )
        },
        tmp_path,
    )
    demo = settings.camera("demo")
    entrance = settings.camera("entrada")
    assert demo is not None and demo.is_synthetic
    assert entrance is not None and entrance.is_network
    assert demo.masked_source() == "synthetic (cámara de demostración)"
    assert "secreto" not in entrance.masked_source()
    assert entrance.masked_source().startswith("rtsp://admin:***@")


def test_cameras_list_with_names_and_disabled(tmp_path):
    payload = [
        {"id": "patio", "name": "Patio trasero", "source": "1"},
        {"name": "Cochera", "source": 2, "enabled": False},
    ]
    settings = _settings({"CAMERAS": json.dumps(payload)}, tmp_path)
    assert [camera.id for camera in settings.cameras] == ["patio", "cochera"]
    assert settings.cameras[0].source == 1
    assert settings.cameras[1].enabled is False
    assert settings.cameras[1].name == "Cochera"
    assert len(settings.enabled_cameras) == 1


@pytest.mark.parametrize(
    "cameras",
    [
        "{no es json}",
        "42",
        '[{"id": "a"}]',
        '[{"id": "a", "source": 0}, {"id": "a", "source": 1}]',
        '[{"id": "a", "source": 0, "enabled": false}]',
    ],
)
def test_invalid_cameras_raise(cameras, tmp_path):
    with pytest.raises(ConfigError):
        _settings({"CAMERAS": cameras}, tmp_path)


def test_invalid_numbers_raise(tmp_path):
    with pytest.raises(ConfigError):
        _settings({"PROCESS_EVERY_N_FRAMES": "muchos"}, tmp_path)
    with pytest.raises(ConfigError):
        _settings({"FACE_TOLERANCE": "2.0"}, tmp_path)
    with pytest.raises(ConfigError):
        _settings({"MCP_TRANSPORT": "carrier-pigeon"}, tmp_path)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN"])
def test_env_float_rejects_non_finite_values(value, tmp_path):
    with pytest.raises(ConfigError, match="finito"):
        _settings({"EVENT_COOLDOWN_SECONDS": value}, tmp_path)


def test_stabilizer_votes_cannot_exceed_window(tmp_path):
    with pytest.raises(ConfigError):
        _settings({"FACE_STABILIZER_WINDOW": "2", "FACE_STABILIZER_MIN_VOTES": "5"}, tmp_path)


def test_transport_http_alias(tmp_path):
    settings = _settings({"MCP_TRANSPORT": "http", "MCP_PORT": "9123"}, tmp_path)
    assert settings.transport == "streamable-http"
    assert settings.port == 9123


def test_zones_seed_disabled_and_detected(tmp_path):
    (tmp_path / "config").mkdir()
    seed = tmp_path / "config" / "zones.json"
    seed.write_text(json.dumps({"zones": []}), encoding="utf-8")

    assert _settings({"ZONES_SEED_FILE": "config/zones.json"}, tmp_path).zones_seed_file == seed
    assert _settings({"ZONES_SEED_FILE": "none"}, tmp_path).zones_seed_file is None
    # Archivo inexistente -> no rompe, simplemente no hay semilla
    assert _settings({"ZONES_SEED_FILE": "config/otro.json"}, tmp_path).zones_seed_file is None


def test_mask_credentials():
    assert (
        mask_credentials("rtsp://usuario:clave@192.168.1.5:554/a")
        == "rtsp://usuario:***@192.168.1.5:554/a"
    )
    assert mask_credentials("0") == "0"
    assert mask_credentials("rtsp://192.168.1.5/stream") == "rtsp://192.168.1.5/stream"


def test_mask_credentials_with_at_sign_and_query_tokens():
    # Contraseñas con @ dentro del userinfo
    assert (
        mask_credentials("rtsp://admin:p@ss@10.0.0.5:554/stream")
        == "rtsp://admin:***@10.0.0.5:554/stream"
    )
    # Tokens en la query string
    masked = mask_credentials(
        "http://cam.local/snap?channel=1&token=abc123&password=secreto&key=k1"
    )
    assert "abc123" not in masked
    assert "secreto" not in masked
    assert "k1" not in masked
    assert "channel=1" in masked


def test_packaged_zones_seed_fallback(tmp_path):
    # Sin config/zones.example.json en el proyecto, se usa el JSON del paquete.
    settings = _settings({"ZONES_SEED_FILE": "config/zones.example.json"}, tmp_path)
    assert settings.zones_seed_file is not None
    assert settings.zones_seed_file.is_file()
    assert settings.zones_seed_file.parent.name == "data"


def test_default_base_dir_honors_env_override(tmp_path, monkeypatch):
    from mcp_security_vision.config import _default_base_dir

    monkeypatch.setenv("MCP_SECURITY_VISION_HOME", str(tmp_path / "home"))
    assert _default_base_dir() == (tmp_path / "home").resolve()


def test_slugify():
    assert slugify("Juan Pérez") == "juan-perez"
    assert slugify("  María   López  ") == "maria-lopez"
    assert slugify("Niño_Ñandú") == "nino-nandu"
    assert slugify("!!!") == ""


def test_camera_config_properties():
    camera = CameraConfig(id="cam1", name="x", source="synthetic")
    assert camera.is_synthetic
    assert not camera.is_network
    assert not camera.is_file
    assert camera.to_dict()["source"].startswith("synthetic")


def test_new_rtsp_and_face_model_options(tmp_path):
    settings = _settings({"RTSP_TRANSPORT": "udp", "FACE_MODEL": "cnn"}, tmp_path)
    assert settings.rtsp_transport == "udp"
    assert settings.face_model == "cnn"

    # valores por defecto pensados para cámaras IP reales
    defaults = _settings({}, tmp_path)
    assert defaults.rtsp_transport == "tcp"
    assert defaults.face_model == "hog"


@pytest.mark.parametrize("valor", ["tcp", "udp", "auto", "none", "default", ""])
def test_rtsp_transport_aliases(valor, tmp_path):
    settings = _settings({"RTSP_TRANSPORT": valor}, tmp_path)
    assert settings.rtsp_transport in {"tcp", "udp", "auto"}


def test_invalid_rtsp_transport_and_face_model(tmp_path):
    with pytest.raises(ConfigError):
        _settings({"RTSP_TRANSPORT": "sctp"}, tmp_path)
    with pytest.raises(ConfigError):
        _settings({"FACE_MODEL": "magic"}, tmp_path)


def test_yolo_imgsz_must_be_multiple_of_32(tmp_path):
    assert _settings({"YOLO_IMGSZ": "480"}, tmp_path).yolo_imgsz == 480  # válido para YOLO
    with pytest.raises(ConfigError, match="múltiplo de 32"):
        _settings({"YOLO_IMGSZ": "500"}, tmp_path)
