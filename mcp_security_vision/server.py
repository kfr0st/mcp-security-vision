#!/usr/bin/env python3
"""Servidor MCP de videovigilancia inteligente.

Expone cámaras (IP por RTSP o analógicas por DVR/USB) a agentes de IA con
herramientas para consultar el estado, leer el historial SQLite, pedir capturas,
registrar rostros y gestionar zonas restringidas (polígonos de intrusión).

Transportes soportados: ``stdio`` (Claude Desktop, Cursor, LangChain...),
``sse`` y ``streamable-http`` (Docker, despliegues remotos).

Uso:

    python server.py                       # stdio (por defecto)
    python server.py --transport streamable-http --host 127.0.0.1 --port 8000
    python server.py --check               # valida la configuración y sale

Compatible con MCP SDK 1.x (``mcp.server.fastmcp.FastMCP``) y 2.x
(``mcp.server.mcpserver.MCPServer``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Compatibilidad MCP SDK 1.x / 2.x
# ---------------------------------------------------------------------------
try:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP, Image  # type: ignore[no-redef]

    MCP_SDK_MAJOR = 1
except ImportError:  # pragma: no cover - depende de la versión instalada
    from mcp.server.mcpserver import Image  # type: ignore[no-redef]
    from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore[no-redef]

    MCP_SDK_MAJOR = 2

from mcp_security_vision import __version__
from mcp_security_vision.config import Settings
from mcp_security_vision.database import EventType
from mcp_security_vision.errors import VisionError
from mcp_security_vision.system import VisionSystem

logger = logging.getLogger("mcp_security_vision.server")

INSTRUCTIONS = """\
Servidor de videovigilancia inteligente con YOLOv8 + reconocimiento facial.

Flujo recomendado para el agente:
1. `list_cameras` para saber qué cámaras existen y su estado.
2. `get_live_status` para saber qué hay ahora delante de la cámara.
3. `capture_snapshot` (con `annotate=true` para ver cajas y zonas) para inspección visual.
4. `get_security_logs` / `get_security_report` para el historial persistente (SQLite).
5. `add_restricted_zone` para definir polígonos de intrusión con coordenadas
   normalizadas (0..1) relativas al ancho y alto de la imagen.
6. `register_current_face` para dar de alta a una persona que esté frente a la cámara.

Todas las horas están en la zona horaria local del servidor.
"""

app = FastMCP(name="SecurityVisionMCP", instructions=INSTRUCTIONS)  # type: ignore[call-arg]

_system: VisionSystem | None = None
_system_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Acceso al sistema de visión
# ---------------------------------------------------------------------------
def set_system(system: VisionSystem | None) -> None:
    """Permite inyectar una instancia (tests) o reemplazarla."""
    global _system
    with _system_lock:
        _system = system


def get_system() -> VisionSystem:
    """Crea el sistema (modelos + cámaras) la primera vez que se usa."""
    global _system
    if _system is None:
        with _system_lock:
            if _system is None:
                settings = Settings.from_env()
                system = VisionSystem(settings)
                system.start()
                _system = system
    return _system


def _error(exc: Exception) -> str:
    return f"⚠️ {exc}"


def _reset_state() -> None:
    """Limpia el contador interno (lo usan los tests entre invocaciones)."""
    if _system is not None:
        _system.pipeline._states.clear()


# ===========================================================================
# HERRAMIENTAS MCP
# ===========================================================================
@app.tool()
def list_cameras() -> str:
    """Lista las cámaras configuradas, su fuente y si están conectadas ahora."""
    try:
        return get_system().cameras_overview()
    except Exception as exc:  # pragma: no cover - defensivo
        return _error(exc)


@app.tool()
def get_live_status(camera_id: str = "") -> str:
    """Estado en tiempo real de la cámara: objetos, rostros y zonas con intrusos.

    Args:
        camera_id: Identificador de la cámara (vacío = cámara por defecto).
    """
    try:
        return get_system().live_status(camera_id)
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


# Sin anotación de retorno: en Python 3.10 FastMCP interpreta ``-> Any`` como
# salida estructurada, envuelve la imagen en ``{"result": ...}`` y falla al
# serializarla. La herramienta devuelve Image o texto de error.
@app.tool()
def capture_snapshot(camera_id: str = "", annotate: bool = False, save: bool = False):
    """Toma una foto en vivo y la devuelve como imagen al agente.

    Permite a agentes multimodales inspeccionar visualmente la escena
    (paquetes en el suelo, matrículas, personas, etc.).

    Args:
        camera_id: Identificador de la cámara (vacío = cámara por defecto).
        annotate: Si es True dibuja cajas de objetos, rostros y zonas restringidas.
        save: Si es True guarda además el JPEG en ``snapshots/`` (por defecto no,
            para no llenar el disco con cada consulta del agente).
    """
    try:
        system = get_system()
        jpeg, _path = system.capture_jpeg(camera_id=camera_id, annotate=annotate, save=save)
        return Image(data=jpeg, format="jpeg")
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def get_security_logs(
    limit: int = 20,
    camera_id: str = "",
    hours: float = 0,
    event_type: str = "",
) -> str:
    """Historial persistente de eventos de seguridad (SQLite).

    Útil para preguntas como «¿pasó alguien hoy?» o «¿a qué hora llegó Carlos?».

    Args:
        limit: Número máximo de eventos a devolver (1-200).
        camera_id: Filtrar por cámara (vacío = todas).
        hours: Filtrar por las últimas N horas (0 = sin filtro temporal).
        event_type: Filtrar por tipo: intrusion, face_known, face_unknown,
            person_unidentified, camera_online, camera_offline, entre otros.
    """
    try:
        kind = (event_type or "").strip().lower() or None
        if kind and kind not in EventType.ALL:
            return f"⚠️ Tipo de evento desconocido: '{kind}'. Válidos: {', '.join(EventType.ALL)}"
        return get_system().get_logs(
            limit=limit,
            camera_id=camera_id or None,
            hours=hours or None,
            event_type=kind,
        )
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def get_security_report(hours: float = 24, camera_id: str = "") -> str:
    """Resumen agregado de la actividad: totales por tipo, rostros y zonas.

    Args:
        hours: Ventana temporal en horas (por defecto 24).
        camera_id: Cámara a resumir (vacío = todas).
    """
    try:
        return get_system().report(hours=hours, camera_id=camera_id or None)
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def get_active_intrusions(camera_id: str = "") -> str:
    """Personas u objetos que están ahora dentro de una zona restringida.

    Args:
        camera_id: Cámara a consultar (vacío = todas).
    """
    try:
        return get_system().active_intrusions(camera_id or None)
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def list_known_people() -> str:
    """Lista las personas registradas en la base de datos de rostros conocidos."""
    try:
        return get_system().known_people()
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def register_current_face(person_name: str, camera_id: str = "") -> str:
    """Registra el rostro que está ahora frente a la cámara como persona conocida.

    Debe haber exactamente una persona en la imagen.

    Args:
        person_name: Nombre visible de la persona (ej. 'María López').
        camera_id: Cámara donde está la persona (vacío = cámara por defecto).
    """
    try:
        return get_system().register_face(person_name, camera_id=camera_id)
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def forget_known_person(person_name: str) -> str:
    """Elimina a una persona del registro facial (foto, memoria y SQLite).

    Args:
        person_name: Nombre de la persona a eliminar.
    """
    try:
        return get_system().forget_face(person_name)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def list_restricted_zones(camera_id: str = "") -> str:
    """Lista las zonas restringidas (polígonos de intrusión) definidas.

    Args:
        camera_id: Filtrar por cámara (vacío = todas).
    """
    try:
        return get_system().list_zones(camera_id=camera_id or None)
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def add_restricted_zone(
    camera_id: str,
    name: str,
    points: list[list[float]],
    labels: list[str] | None = None,
    enabled: bool = True,
) -> str:
    """Crea o actualiza una zona restringida y empieza a vigilar intrusiones.

    Las coordenadas van **normalizadas** (0..1) respecto al ancho y alto de la imagen:
    (0,0) es la esquina superior izquierda y (1,1) la inferior derecha.

    Args:
        camera_id: Cámara a la que pertenece la zona.
        name: Nombre único de la zona (ej. 'Puerta trasera').
        points: Polígono como lista de [x, y] normalizados, mínimo 3 puntos.
            Ejemplo: [[0.35, 0.30], [0.65, 0.30], [0.65, 0.95], [0.35, 0.95]]
        labels: Objetos que disparan la alarma (por defecto ["person"]).
        enabled: Si es False la zona se guarda pero no se vigila.
    """
    try:
        return get_system().add_zone(
            camera_id=camera_id,
            name=name,
            points=points,
            labels=tuple(labels or ["person"]),
            enabled=enabled,
        )
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def remove_restricted_zone(camera_id: str, name: str) -> str:
    """Elimina una zona restringida de una cámara.

    Args:
        camera_id: Cámara de la zona.
        name: Nombre de la zona a eliminar.
    """
    try:
        return get_system().remove_zone(camera_id=camera_id, name=name)
    except VisionError as exc:
        return _error(exc)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


@app.tool()
def cleanup_history(
    keep_days: float = 30, purge_snapshots: bool = False, confirm: bool = False
) -> str:
    """Herramienta de mantenimiento: borra eventos antiguos (y capturas si se pide).

    Es una operación destructiva. Debes llamarla primero con ``confirm=false``
    para revisar el efecto y luego repetirla con ``confirm=true``.

    Args:
        keep_days: Días de historial a conservar (mínimo 1).
        purge_snapshots: Si es True, borra también las capturas antiguas.
        confirm: Debe ser True para ejecutar la limpieza.
    """
    try:
        keep_days = float(keep_days)
        if not confirm:
            extra = " y las capturas" if purge_snapshots else ""
            return (
                "⚠️ Limpieza no ejecutada. Esta operación borrará eventos"
                f"{extra} con más de {keep_days:g} días.\n"
                "Si estás seguro, repite la llamada con `confirm=true`."
            )
        return get_system().cleanup(keep_days=keep_days, purge_snapshots=purge_snapshots)
    except Exception as exc:  # pragma: no cover
        return _error(exc)


try:  # El decorador de recursos existe en ambas versiones del SDK

    @app.resource("security://status")
    def status_resource() -> str:
        """Resumen JSON del sistema (cámaras, personas, zonas y últimos eventos)."""
        return json.dumps(get_system().status_summary(), ensure_ascii=False, indent=2)
except Exception:  # pragma: no cover - SDK sin recursos
    status_resource = None  # type: ignore[assignment]


# ===========================================================================
# Arranque del servidor
# ===========================================================================
_DEFAULT_ALLOWED_HOSTS = (
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "[::1]",
    "[::1]:*",
)
_DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://[::1]:*",
    "https://localhost:*",
    "https://127.0.0.1:*",
    "https://[::1]:*",
)


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in (os.getenv(name) or "").split(",") if item.strip()]


def _transport_security() -> Any | None:
    """Restringe hosts/orígenes permitidos (protección contra DNS rebinding).

    Por defecto solo se aceptan hosts locales. Para publicar detrás de un proxy,
    añade el dominio a ``MCP_ALLOWED_HOSTS`` y su origen a ``MCP_ALLOWED_ORIGINS``.
    """
    allowed_hosts = list(dict.fromkeys([*_DEFAULT_ALLOWED_HOSTS, *_env_list("MCP_ALLOWED_HOSTS")]))
    allowed_origins = list(
        dict.fromkeys([*_DEFAULT_ALLOWED_ORIGINS, *_env_list("MCP_ALLOWED_ORIGINS")])
    )
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        return TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
    except Exception:  # pragma: no cover - versión antigua del SDK
        logger.warning(
            "SDK sin soporte de transport_security: no se puede validar el Host. "
            "Actualiza mcp a >=1.9 o no expongas el transporte HTTP."
        )
        return None


def port_in_use(host: str, port: int) -> bool:
    """¿Está ocupado el puerto? (se comprueba antes de cargar los modelos)."""
    bind_host = "0.0.0.0" if host in {"", "0.0.0.0", "::", "*"} else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((bind_host, int(port)))
        return False
    except OSError:
        return True


def _print_port_help(host: str, port: int) -> None:
    print(
        f"[ERROR] El puerto {port} ya está en uso"
        f"{' en ' + host if host else ''}.\n"
        f"  · Cambia el puerto:   MCP_PORT={int(port) + 1} python server.py --transport streamable-http\n"
        "  · O libera el puerto:\n"
        f"      Linux/macOS: lsof -i :{port}   (o)  ss -ltnp | grep {port}\n"
        f"      Windows:     netstat -ano | findstr :{port}   y luego  taskkill /PID <PID> /F\n"
        "  · Si usas Docker:    docker ps  y para el contenedor que lo ocupa.",
        file=sys.stderr,
    )


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    """Ejecuta el servidor MCP adaptándose a la versión del SDK instalada."""
    security = _transport_security() if transport != "stdio" else None
    legacy_settings = getattr(app, "settings", None)
    if legacy_settings is not None and MCP_SDK_MAJOR == 1:
        legacy_settings.host = host
        legacy_settings.port = port
        if security is not None:
            legacy_settings.transport_security = security
        app.run(transport=transport)  # type: ignore[call-arg]
        return

    kwargs: dict[str, Any] = {} if transport == "stdio" else {"host": host, "port": port}
    if security is not None:
        kwargs["transport_security"] = security
    app.run(transport=transport, **kwargs)  # type: ignore[call-arg]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Servidor MCP de videovigilancia inteligente")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default=None)
    parser.add_argument(
        "--host",
        default=None,
        help="Host para sse/streamable-http (127.0.0.1 por defecto; 0.0.0.0 solo dentro de Docker/proxy)",
    )
    parser.add_argument("--port", type=int, default=None, help="Puerto para sse/streamable-http")
    parser.add_argument("--check", action="store_true", help="Valida configuración y sale")
    parser.add_argument("--version", action="version", version=f"mcp-security-vision {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = Settings.from_env()
    if args.transport:
        settings.transport = args.transport
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    # stdio usa stdout para JSON-RPC: TODOS los logs van a stderr.
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    banner = (
        f"mcp-security-vision {__version__} · MCP SDK {MCP_SDK_MAJOR}.x · "
        f"transporte={settings.transport}"
    )
    print(banner, file=sys.stderr)
    print(
        "Cámaras: "
        + ", ".join(f"{cam.id}={cam.masked_source()}" for cam in settings.enabled_cameras),
        file=sys.stderr,
    )

    if args.check:
        settings.ensure_directories()
        print(f"SQLite: {settings.db_path}", file=sys.stderr)
        print(f"known_faces: {settings.known_faces_dir}", file=sys.stderr)
        print(f"snapshots: {settings.snapshots_dir}", file=sys.stderr)
        print("Configuración válida ✅", file=sys.stderr)
        return 0

    if settings.transport != "stdio" and port_in_use(settings.host, settings.port):
        _print_port_help(settings.host, settings.port)
        return 1

    system = VisionSystem(settings)
    set_system(system)
    system.start()
    try:
        run_server(transport=settings.transport, host=settings.host, port=settings.port)
    except KeyboardInterrupt:  # pragma: no cover
        print("Interrumpido por el usuario", file=sys.stderr)
    finally:
        system.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
