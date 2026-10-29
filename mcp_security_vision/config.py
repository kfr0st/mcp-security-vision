"""Configuración central del sistema.

Toda la configuración se lee de variables de entorno (o de un archivo ``.env``)
para que nunca haya credenciales de cámaras dentro del repositorio.

Ejemplos de ``CAMERAS`` (JSON)::

    # Varias cámaras: IP (RTSP) + analógica por capturadora USB + demo
    CAMERAS={"entrada": "rtsp://user:pass@192.168.1.50:554/stream1", "patio": 0, "demo": "synthetic"}

    # Equivalente con nombres "bonitos"
    CAMERAS=[{"id": "entrada", "name": "Puerta principal", "source": "rtsp://..."},
             {"id": "patio", "name": "Patio trasero", "source": "0"}]
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:  # python-dotenv es opcional para poder usar el paquete en tests
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:  # type: ignore[misc]
        return False


from .errors import ConfigError


def _default_base_dir() -> Path:
    """Directorio de datos por defecto.

    - ``MCP_SECURITY_VISION_HOME`` tiene prioridad.
    - En el repo (hay ``pyproject.toml``) se usa la raíz del proyecto.
    - Instalado con pip se usa un directorio de datos del usuario, no
      ``site-packages`` (que suele ser de solo lectura).
    """
    override = (os.getenv("MCP_SECURITY_VISION_HOME") or "").strip()
    if override:
        return Path(override).expanduser().resolve()

    source_root = Path(__file__).resolve().parent.parent
    if (source_root / "pyproject.toml").is_file():
        return source_root

    if os.name == "nt":
        root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return root / "mcp-security-vision"


BASE_DIR = _default_base_dir()

DEFAULT_DB_PATH = "data/security.db"
DEFAULT_KNOWN_FACES_DIR = "known_faces"
DEFAULT_SNAPSHOTS_DIR = "snapshots"
DEFAULT_ZONES_SEED = "config/zones.example.json"
DEFAULT_YOLO_MODEL = "yolov8n.pt"

_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^/\s@]*:[^/\s]*)@"
)
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|password|passwd|pwd|key|auth|secret|access_token)=)[^&\s#]+"
)
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

_TRUE_VALUES = {"1", "true", "yes", "y", "on", "si", "sí"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


# ---------------------------------------------------------------------------
# Utilidades de parseo
# ---------------------------------------------------------------------------
def mask_credentials(value: str) -> str:
    """Oculta la contraseña y los tokens de una URL RTSP/HTTP para logs.

    Soporta contraseñas con ``@`` (por ejemplo ``p@ss``) y redacta parámetros
    sensibles de la query string (``token``, ``password``, ``key``, etc.).
    """
    if not isinstance(value, str):
        return str(value)

    def _mask_url(match: re.Match[str]) -> str:
        userinfo = match["userinfo"]
        user = userinfo.split(":", 1)[0]
        return f"{match['scheme']}{user}:***@"

    masked = _CREDENTIALS_RE.sub(_mask_url, value)
    return _SENSITIVE_QUERY_RE.sub(r"\1***", masked)


def slugify(value: str) -> str:
    """Convierte 'Juan Pérez' en 'juan-perez' (seguro para nombres de archivo)."""
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[\s_]+", "-", text)
    text = _SLUG_RE.sub("", text)
    return text.strip("-")


def _env_int(env: Mapping[str, str], key: str, default: int, minimum: int | None = None) -> int:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} debe ser un número entero (recibido: {raw!r})") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} debe ser >= {minimum} (recibido: {value})")
    return value


def _env_float(
    env: Mapping[str, str],
    key: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} debe ser un número (recibido: {raw!r})") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{key} debe ser un número finito (recibido: {raw!r})")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} debe ser >= {minimum} (recibido: {value})")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{key} debe ser <= {maximum} (recibido: {value})")
    return value


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or isinstance(value, bool):
        return default if value is None else value
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    if key not in env:
        return default
    return _as_bool(env.get(key), default)


def _resolve(path_value: str | Path, base: Path) -> Path:
    path = Path(str(path_value)).expanduser()
    return path if path.is_absolute() else (base / path)


def _parse_source(raw: Any) -> Any:
    """Normaliza la fuente de vídeo: índice USB, URL RTSP/HTTP o 'synthetic'."""
    if isinstance(raw, bool):
        raise ConfigError("La fuente de vídeo no puede ser un booleano")
    if isinstance(raw, int):
        return raw
    if raw is None:
        raise ConfigError("Cada cámara necesita un 'source'")
    text = str(raw).strip()
    if not text:
        raise ConfigError("La fuente de vídeo está vacía")
    if text.isdigit():
        return int(text)
    if text.lower() in {"synthetic", "demo", "simulada", "fake"}:
        return "synthetic"
    return text


# ---------------------------------------------------------------------------
# Cámaras
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CameraConfig:
    """Definición de una fuente de vídeo."""

    id: str
    name: str
    source: Any
    enabled: bool = True

    @property
    def is_synthetic(self) -> bool:
        return self.source == "synthetic"

    @property
    def is_network(self) -> bool:
        return isinstance(self.source, str) and self.source.lower().startswith(
            ("rtsp://", "rtsps://", "http://", "https://")
        )

    @property
    def is_file(self) -> bool:
        if not isinstance(self.source, str) or self.source == "synthetic":
            return False
        if self.is_network:
            return False
        return Path(self.source).is_file()

    def masked_source(self) -> str:
        """Fuente de vídeo segura para logs (sin contraseña)."""
        if self.is_synthetic:
            return "synthetic (cámara de demostración)"
        return mask_credentials(str(self.source))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.masked_source(),
            "enabled": self.enabled,
        }


def _parse_cameras(env: Mapping[str, str]) -> list[CameraConfig]:
    raw = (env.get("CAMERAS") or "").strip()
    if not raw:
        return [
            CameraConfig(
                id=(env.get("CAMERA_ID") or "cam1").strip(),
                name=(env.get("CAMERA_NAME") or "Cámara principal").strip(),
                source=_parse_source(env.get("CAMERA_SOURCE") or "0"),
            )
        ]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"CAMERAS no es un JSON válido: {exc}") from exc

    if isinstance(data, dict):
        entries: list[Any] = [
            {"id": key, "name": key, "source": value} for key, value in data.items()
        ]
    elif isinstance(data, list):
        entries = data
    else:
        raise ConfigError("CAMERAS debe ser un objeto {id: source} o una lista de cámaras")

    cameras: list[CameraConfig] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, (str, int)) and not isinstance(entry, bool):
            entry = {"source": entry}
        if not isinstance(entry, dict):
            raise ConfigError(f"CAMERAS[{index}] debe ser un objeto con 'source'")
        if "source" not in entry:
            raise ConfigError(f"CAMERAS[{index}] no tiene la clave 'source'")

        cam_id = str(entry.get("id") or slugify(str(entry.get("name") or "")) or f"cam{index}")
        if cam_id in seen:
            raise ConfigError(f"Id de cámara duplicado en CAMERAS: {cam_id!r}")
        seen.add(cam_id)
        cameras.append(
            CameraConfig(
                id=cam_id,
                name=str(entry.get("name") or cam_id),
                source=_parse_source(entry["source"]),
                enabled=_as_bool(entry.get("enabled", True), True),
            )
        )

    if not any(c.enabled for c in cameras):
        raise ConfigError("Todas las cámaras de CAMERAS están deshabilitadas")
    return cameras


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    """Configuración completa del servidor de visión."""

    cameras: list[CameraConfig]
    # --- Procesamiento ---
    process_every_n_frames: int = 5
    yolo_model: str = DEFAULT_YOLO_MODEL
    yolo_conf: float = 0.5
    yolo_imgsz: int = 640
    yolo_device: str | None = None
    faces_enabled: bool = True
    face_model: str = "hog"
    face_tolerance: float = 0.6
    face_scale: float = 0.25
    face_stabilizer_window: int = 3
    face_stabilizer_min_votes: int = 2
    faces_without_person: bool = False
    # --- Eventos ---
    event_cooldown_seconds: float = 20.0
    intrusion_cooldown_seconds: float = 30.0
    presence_timeout_seconds: float = 2.5
    snapshot_on_alert: bool = True
    # --- Rutas ---
    db_path: Path = field(default_factory=lambda: BASE_DIR / DEFAULT_DB_PATH)
    known_faces_dir: Path = field(default_factory=lambda: BASE_DIR / DEFAULT_KNOWN_FACES_DIR)
    snapshots_dir: Path = field(default_factory=lambda: BASE_DIR / DEFAULT_SNAPSHOTS_DIR)
    zones_seed_file: Path | None = None
    # --- Cámara / reconexión ---
    reconnect_delay_seconds: float = 5.0
    max_reconnect_delay_seconds: float = 60.0
    rtsp_transport: str = "tcp"
    # --- Servidor MCP ---
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    # --- Notificaciones (opcional) ---
    alert_webhook_url: str | None = None
    alert_webhook_min_severity: str = "warning"
    # --- Modo demo / CI ---
    fake_models: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        base_dir: Path | None = None,
        load_dotenv_file: bool = True,
    ) -> Settings:
        """Construye la configuración a partir del entorno."""
        if env is None:
            if load_dotenv_file:
                load_dotenv()
            env = os.environ
        env = dict(env)
        base = Path(base_dir) if base_dir is not None else BASE_DIR

        zones_seed_raw = (env.get("ZONES_SEED_FILE") or DEFAULT_ZONES_SEED).strip()
        zones_seed: Path | None = None
        if zones_seed_raw.lower() not in {"", "none", "off", "false", "0"}:
            candidate = _resolve(zones_seed_raw, base)
            if not candidate.is_file() and zones_seed_raw == DEFAULT_ZONES_SEED:
                # Paquete instalado: la semilla viaja como package data.
                packaged = Path(__file__).resolve().parent / "data" / "zones.example.json"
                if packaged.is_file():
                    candidate = packaged
            zones_seed = candidate if candidate.is_file() else None

        rtsp_transport = (env.get("RTSP_TRANSPORT") or "tcp").strip().lower()
        if rtsp_transport in {"", "default", "none"}:
            rtsp_transport = "auto"
        if rtsp_transport not in {"tcp", "udp", "auto"}:
            raise ConfigError("RTSP_TRANSPORT debe ser tcp, udp o auto")

        face_model = (env.get("FACE_MODEL") or "hog").strip().lower()
        if face_model not in {"hog", "cnn"}:
            raise ConfigError("FACE_MODEL debe ser 'hog' (rápido, CPU) o 'cnn' (más preciso, GPU)")

        transport = (env.get("MCP_TRANSPORT") or "stdio").strip().lower()
        if transport not in {"stdio", "sse", "streamable-http", "http"}:
            raise ConfigError("MCP_TRANSPORT debe ser stdio, sse o streamable-http")
        if transport == "http":
            transport = "streamable-http"

        webhook = (env.get("ALERT_WEBHOOK_URL") or "").strip() or None
        severity = (env.get("ALERT_WEBHOOK_MIN_SEVERITY") or "warning").strip().lower()
        if severity not in {"info", "warning", "critical"}:
            raise ConfigError("ALERT_WEBHOOK_MIN_SEVERITY debe ser info, warning o critical")

        settings = cls(
            cameras=_parse_cameras(env),
            process_every_n_frames=_env_int(env, "PROCESS_EVERY_N_FRAMES", 5, minimum=1),
            yolo_model=(env.get("YOLO_MODEL") or DEFAULT_YOLO_MODEL).strip(),
            yolo_conf=_env_float(env, "YOLO_CONF", 0.5, 0.0, 1.0),
            yolo_imgsz=_env_int(env, "YOLO_IMGSZ", 640, minimum=32),
            yolo_device=(env.get("YOLO_DEVICE") or "").strip() or None,
            faces_enabled=_env_bool(env, "FACES_ENABLED", True),
            face_model=face_model,
            face_tolerance=_env_float(env, "FACE_TOLERANCE", 0.6, 0.1, 1.0),
            face_scale=_env_float(env, "FACE_SCALE", 0.25, 0.05, 1.0),
            face_stabilizer_window=_env_int(env, "FACE_STABILIZER_WINDOW", 3, minimum=1),
            face_stabilizer_min_votes=_env_int(env, "FACE_STABILIZER_MIN_VOTES", 2, minimum=1),
            faces_without_person=_env_bool(env, "FACES_WITHOUT_PERSON", False),
            event_cooldown_seconds=_env_float(env, "EVENT_COOLDOWN_SECONDS", 20.0, minimum=0),
            intrusion_cooldown_seconds=_env_float(
                env, "INTRUSION_COOLDOWN_SECONDS", 30.0, minimum=0
            ),
            presence_timeout_seconds=_env_float(env, "PRESENCE_TIMEOUT_SECONDS", 2.5, minimum=0),
            snapshot_on_alert=_env_bool(env, "SNAPSHOT_ON_ALERT", True),
            db_path=_resolve(env.get("DB_PATH") or DEFAULT_DB_PATH, base),
            known_faces_dir=_resolve(env.get("KNOWN_FACES_DIR") or DEFAULT_KNOWN_FACES_DIR, base),
            snapshots_dir=_resolve(env.get("SNAPSHOTS_DIR") or DEFAULT_SNAPSHOTS_DIR, base),
            zones_seed_file=zones_seed,
            reconnect_delay_seconds=_env_float(env, "RECONNECT_DELAY_SECONDS", 5.0, minimum=0.05),
            max_reconnect_delay_seconds=_env_float(
                env, "MAX_RECONNECT_DELAY_SECONDS", 60.0, minimum=1
            ),
            rtsp_transport=rtsp_transport,
            transport=transport,
            host=(env.get("MCP_HOST") or "127.0.0.1").strip(),
            port=_env_int(env, "MCP_PORT", 8000, minimum=1),
            alert_webhook_url=webhook,
            alert_webhook_min_severity=severity,
            fake_models=_env_bool(env, "VISION_FAKE_MODELS", False),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        )
        if settings.face_stabilizer_min_votes > settings.face_stabilizer_window:
            raise ConfigError(
                "FACE_STABILIZER_MIN_VOTES no puede ser mayor que FACE_STABILIZER_WINDOW"
            )
        if settings.yolo_imgsz % 32 != 0:
            raise ConfigError(
                "YOLO_IMGSZ debe ser múltiplo de 32 (por ejemplo 320, 480, 512 o 640): "
                f"recibido {settings.yolo_imgsz}"
            )
        return settings

    # ------------------------------------------------------------------
    @property
    def enabled_cameras(self) -> list[CameraConfig]:
        return [camera for camera in self.cameras if camera.enabled]

    def camera(self, camera_id: str) -> CameraConfig | None:
        for camera in self.cameras:
            if camera.id == camera_id:
                return camera
        return None

    def default_camera(self) -> CameraConfig:
        cameras = self.enabled_cameras
        if not cameras:
            raise ConfigError("No hay cámaras habilitadas")
        return cameras[0]

    def with_overrides(self, **overrides: Any) -> Settings:
        """Copia inmutable-friendly para tests."""
        return replace(self, **overrides)

    def ensure_directories(self) -> None:
        for path in (self.db_path.parent, self.known_faces_dir, self.snapshots_dir):
            Path(path).mkdir(parents=True, exist_ok=True)
