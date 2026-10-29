#!/usr/bin/env python3
"""Editor visual de zonas restringidas (polígonos de intrusión).

Abre una imagen en vivo de la cámara (o un archivo) y permite dibujar el
polígono con el ratón. Las coordenadas se guardan normalizadas (0..1) en SQLite,
así que la zona vale para cualquier resolución.

Requisitos: OpenCV con interfaz gráfica (``opencv-python``, no ``-headless``) y
un entorno con pantalla (en servidores usa ``ssh -X`` o define la zona con la
herramienta MCP ``add_restricted_zone``).

Uso:
    python scripts/draw_zones.py --camera-id cam1 --name "Puerta trasera"
    python scripts/draw_zones.py --image foto.jpg --camera-id cam1 --name "Cochera"

Controles:
    clic izquierdo  añadir vértice
    u               deshacer último vértice
    c               borrar todos los vértices
    s               guardar zona en SQLite y salir
    q / ESC         salir sin guardar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from mcp_security_vision.camera import open_capture
from mcp_security_vision.config import Settings
from mcp_security_vision.database import Database, EventType
from mcp_security_vision.zones import ZoneStore, polygon_to_pixels

WINDOW = "MCP Security Vision - dibuja la zona (s=guardar, u=deshacer, q=salir)"


class ZoneDrawer:
    def __init__(self, frame, existing_zones) -> None:
        self.base = frame
        self.points_px: list[tuple[int, int]] = []
        self.existing = existing_zones

    def on_mouse(self, event, x, y, _flags, _param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points_px.append((x, y))

    def render(self):
        canvas = self.base.copy()
        height, width = canvas.shape[:2]
        for zone in self.existing:
            pixels = polygon_to_pixels(zone.points, width, height)
            if len(pixels) >= 3:
                cv2.polylines(canvas, [np.array(pixels, dtype="int32")], True, (120, 120, 120), 1)
                cv2.putText(
                    canvas, zone.name, pixels[0], cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1
                )
        if self.points_px:
            for point in self.points_px:
                cv2.circle(canvas, point, 4, (0, 200, 255), -1)
            if len(self.points_px) > 1:
                cv2.polylines(
                    canvas, [np.array(self.points_px, dtype="int32")], False, (0, 200, 255), 2
                )
            if len(self.points_px) > 2:
                cv2.line(canvas, self.points_px[-1], self.points_px[0], (0, 200, 255), 1)
        cv2.putText(
            canvas,
            f"vertices: {len(self.points_px)} | s=guardar  u=deshacer  c=limpiar  q=salir",
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        return canvas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dibuja zonas restringidas sobre una imagen de la cámara"
    )
    parser.add_argument(
        "--camera-id", default=None, help="Id de la cámara (por defecto, la primera)"
    )
    parser.add_argument("--name", default="Zona nueva", help="Nombre de la zona")
    parser.add_argument("--labels", default="person", help="Objetos que disparan la alarma (coma)")
    parser.add_argument("--image", default=None, help="Usar una imagen fija en lugar de la cámara")
    parser.add_argument(
        "--disabled", dest="enabled", action="store_false", help="Guardar la zona sin vigilarla"
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    settings.ensure_directories()
    camera_id = args.camera_id or settings.default_camera().id

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"No se pudo leer la imagen: {args.image}", file=sys.stderr)
            return 1
    else:
        camera = settings.camera(camera_id)
        if camera is None:
            print(f"Cámara desconocida: {camera_id}", file=sys.stderr)
            return 1
        capture = open_capture(camera.source, settings)
        if capture is None:
            print(f"No se pudo abrir la cámara {camera_id}", file=sys.stderr)
            return 1
        try:
            ok, frame = capture.read()
        finally:
            capture.release()
        if not ok or frame is None:
            print("La cámara no entregó ningún fotograma", file=sys.stderr)
            return 1

    db = Database(settings.db_path)
    store = ZoneStore(db, seed_file=settings.zones_seed_file)
    store.seed()
    existing = store.for_camera(camera_id)

    drawer = ZoneDrawer(frame, existing)
    try:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    except cv2.error:
        print(
            "No hay entorno gráfico disponible. Usa la herramienta MCP add_restricted_zone\n"
            "o ejecuta este script en un equipo con pantalla (opencv-python, no -headless).",
            file=sys.stderr,
        )
        return 1
    cv2.setMouseCallback(WINDOW, drawer.on_mouse)

    while True:
        cv2.imshow(WINDOW, drawer.render())
        key = cv2.waitKey(20) & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("u") and drawer.points_px:
            drawer.points_px.pop()
        if key == ord("c"):
            drawer.points_px.clear()
        if key == ord("s"):
            if len(drawer.points_px) < 3:
                print("Necesitas al menos 3 vértices para guardar.", file=sys.stderr)
                continue
            height, width = frame.shape[:2]
            normalized = [(x / width, y / height) for x, y in drawer.points_px]
            labels = tuple(item.strip() for item in args.labels.split(",") if item.strip())
            zone = store.add(
                camera_id=camera_id,
                name=args.name,
                points=normalized,
                labels=labels or ("person",),
                enabled=args.enabled,
            )
            db.log_event(
                camera_id=camera_id,
                event_type=EventType.ZONE_ADDED,
                description=f"Zona restringida definida con el editor visual: {zone.name}",
                zone_names=(zone.name,),
                metadata={"source": "scripts/draw_zones.py"},
            )
            print(f"Zona '{zone.name}' guardada en {camera_id}: {zone.points}")
            break

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
