#!/usr/bin/env python3
"""Diagnóstico rápido: valida .env, rutas, modelos y conexión a las cámaras.

Uso:
    python scripts/check_setup.py
    python scripts/check_setup.py --read-frames     # además lee 1 fotograma por cámara

Devuelve código de salida 0 si todo está correcto y 1 si hay algún problema,
de modo que se pueda usar en CI o en un script de instalación.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_security_vision.config import Settings
from mcp_security_vision.database import Database
from mcp_security_vision.errors import ConfigError


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Comprueba la instalación de MCP Security Vision")
    parser.add_argument(
        "--read-frames", action="store_true", help="Intenta leer un fotograma de cada cámara"
    )
    args = parser.parse_args(argv)

    print("=" * 66)
    print(" MCP SECURITY VISION · diagnóstico de instalación")
    print("=" * 66)

    try:
        settings = Settings.from_env()
        settings.ensure_directories()
    except ConfigError as exc:
        print(f"\n[FALLO] Configuración inválida: {exc}")
        return 1

    problems: list[str] = []
    print(f"\n[OK] Configuración cargada. Transporte MCP: {settings.transport}")
    print(f"     SQLite     : {settings.db_path}")
    print(f"     known_faces: {settings.known_faces_dir}")
    print(f"     snapshots  : {settings.snapshots_dir}")
    print(f"     zonas seed : {settings.zones_seed_file or '(ninguna)'}")

    print("\nCámaras configuradas:")
    for camera in settings.cameras:
        state = "habilitada" if camera.enabled else "deshabilitada"
        print(f"  - {camera.id} ({camera.name}) -> {camera.masked_source()} [{state}]")

    print("\nModelos / dependencias:")
    checks = {
        "ultralytics (YOLOv8)": _module_available("ultralytics"),
        "face_recognition (dlib)": _module_available("face_recognition"),
        "opencv (cv2)": _module_available("cv2"),
        "mcp": _module_available("mcp"),
    }
    required = {"opencv (cv2)", "mcp"}
    if not settings.fake_models:
        required.add("ultralytics (YOLOv8)")
        if settings.faces_enabled:
            required.add("face_recognition (dlib)")
    for name, available in checks.items():
        flag = "OK " if available else "FALTA"
        suffix = "" if available or settings.fake_models else "  <- instala requirements.txt"
        print(f"  [{flag}] {name}{suffix}")
        if not available and name in required:
            problems.append(f"Falta la dependencia obligatoria: {name}")
    if settings.fake_models:
        print("  [INFO] VISION_FAKE_MODELS=1: se usarán modelos simulados (modo demo).")
    if not settings.faces_enabled:
        print("  [INFO] FACES_ENABLED=false: no se usa dlib ni reconocimiento facial.")

    try:
        db = Database(settings.db_path)
        zones = db.get_zones()
        people = db.get_people()
    except Exception as exc:
        print(f"\n[FALLO] No se pudo abrir la base de datos {settings.db_path}: {exc}")
        return 1

    print(f"\nBase de datos lista (schema v{db.schema_version()}):")
    print(f"  - eventos guardados: {db.count_events()}")
    print(f"  - personas conocidas: {len(people)}")
    for person in people:
        print(f"      · {person.name} ({person.file_name})")
    print(f"  - zonas restringidas: {len(zones)}")
    for zone in zones:
        enabled = "activa" if zone.enabled else "deshabilitada"
        print(f"      · [{zone.camera_id}] {zone.name} ({enabled})")

    if args.read_frames:
        from mcp_security_vision.camera import open_capture

        print("\nPrueba de lectura de cámaras:")
        for camera in settings.enabled_cameras:
            capture = open_capture(camera.source, settings)
            if capture is None:
                print(f"  [FALLO] {camera.id}: no se pudo abrir {camera.masked_source()}")
                problems.append(f"No se pudo abrir la cámara '{camera.id}'")
                continue
            try:
                ok, frame = capture.read()
                if ok and frame is not None:
                    height, width = frame.shape[:2]
                    print(f"  [OK] {camera.id}: fotograma {width}x{height}")
                else:
                    print(f"  [FALLO] {camera.id}: abrió pero no entregó fotogramas")
                    problems.append(f"La cámara '{camera.id}' no entregó fotogramas")
            finally:
                capture.release()

    if problems:
        print(f"\n[FALLO] Diagnóstico con {len(problems)} problema(s):")
        for problem in problems:
            print(f"  · {problem}")
        return 1

    print("\nTodo listo. Arranca con:  python server.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
