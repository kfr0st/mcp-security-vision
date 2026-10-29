# Changelog

Todas las modificaciones relevantes de este proyecto.
El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el versionado es [SemVer](https://semver.org/lang/es/).

## [2.1.0] — 2026-10-29

Revisión de seguridad, robustez y preparación para publicar. **144 tests** en verde
con MCP SDK 1.30.0 y 2.3.0.

> **Nota de actualización:** `cleanup_history` exige `confirm=true`,
> `capture_snapshot` ya no guarda JPEG por defecto y la base de datos migra al
> esquema v2 (deduplicado de zonas sin distinguir mayúsculas).

### Seguridad

- El transporte HTTP se documenta y se publica solo en `127.0.0.1` por defecto
  (Docker, Makefile y README); se advierte de que no incluye autenticación.
- `MCP_ALLOWED_HOSTS` y `MCP_ALLOWED_ORIGINS` validan `Host`/`Origin` con valores
  locales por defecto para mitigar *DNS rebinding*.
- `cleanup_history` exige `confirm=true` y `cleanup()` no borra capturas salvo que
  se pida `purge_snapshots=true`; se rechazan días negativos, `NaN` e `inf`.
- El enmascarado de credenciales soporta contraseñas con `@` y redacta tokens de
  la query string (`token`, `password`, `key`, etc.).
- `check_setup.py` devuelve código de salida 1 si falta una dependencia o falla
  una cámara, para poder usarlo en CI.

### Corregido

- Las zonas caducadas desaparecen de `active()` y el estado se poda.
- El seed de zonas normaliza `labels` en string y `enabled: "false"`.
- `configure_repo.py` actualiza también `docker-compose.yml` y `Makefile`.
- `FACES_ENABLED=false` ya no importa dlib ni crea el reconocedor.
- Instalación con `pip`: datos en un directorio de usuario y semilla de zonas
  empaquetada como *package data*.
- El registro facial guarda solo el recorte del rostro y normaliza slugs con
  mayúsculas/espacios.
- Inferencia de YOLO/dlib serializada entre cámaras; `EventThrottle` poda claves.
- La cámara aplica backoff exponencial real, limita eventos online/offline y
  sobrevive a fallos de SQLite al registrar.
- `VisionSystem` puede reiniciarse (`stop()` + `start()`) recreando workers.
- Zonas únicas sin distinguir mayúsculas (esquema v2 con migración y deduplicado).
- `_env_float` rechaza `NaN`/`inf`; `capture_snapshot` no guarda JPEG salvo
  `save=true`.
- El workflow de Docker usa un nombre de imagen con *fallback* y ejecuta tests
  antes de publicar.

### Documentación y comunidad

- README con índice, soporte Python 3.10–3.12, parámetros nuevos y checklist de
  producción; se suaviza la afirmación «seguro por diseño».
- Nuevos `SECURITY.md`, `CONTRIBUTING.md`, plantillas de issue y de pull request.
- Placeholders sustituidos por la cuenta `kfr0st` y guía de publicación actualizada.

## [2.0.1] — 2026-10-26

Versión centrada en **verificar la guía de problemas frecuentes contra el código real**
y en dejar el repositorio listo para publicar.

### Corregido

- **Guía de problemas frecuentes imprecisa o falsa.** Se comprobó cada fila ejecutando el
  proyecto: el mensaje `[WARN] Señal de cámara perdida` **no existía**; ahora sí hay avisos
  claros (`[cam1] Cámara SIN SEÑAL: … Reintentando en 5s...`, `Primer fotograma recibido`,
  `RECONECTADA`) con logging y registro en SQLite.
- **Cámara que no abre**: antes solo quedaba un evento `camera_offline` en la base de datos y
  la consola únicamente mostraba el error crudo de FFmpeg. Ahora se explica qué pasa y cuándo
  se reintenta.
- **`open_capture` podía romper en builds de OpenCV sin `CAP_PROP_OPEN_TIMEOUT_MSEC`**: los
  parámetros se construyen comprobando `getattr(cv2, …)` antes de usarlos.
- **Transporte RTSP**: se fuerza `rtsp_transport=tcp` por defecto (muchas cámaras pierden
  paquetes con UDP y entran en bucle de «sin señal»). Configurable con `RTSP_TRANSPORT`;
  se respeta `OPENCV_FFMPEG_CAPTURE_OPTIONS` si el usuario ya lo definió.
- **`Address already in use`**: el puerto se comprueba **antes** de cargar YOLO/dlib y se
  muestra un mensaje accionable (cambiar `MCP_PORT`, liberar el puerto con `lsof`/`netstat`
  o parar el contenedor). Antes era la traza de uvicorn con código de salida 3.
- **`YOLO_IMGSZ`** ahora se valida: debe ser múltiplo de 32 (320/480/512/640). Un valor como
  `500` hacía que Ultralytics lo reescribiera con un warning poco visible.
- **Zonas de intrusión**: se documenta y se recuerda en la respuesta de `add_restricted_zone`
  que el punto evaluado es el **centro inferior de la caja (los pies)**, no la cabeza; era la
  causa principal de «la zona nunca dispara».

### Añadido

- `FACE_MODEL` (`hog`/`cnn`) para elegir precisión vs. velocidad del reconocimiento facial.
- `docs/TROUBLESHOOTING.md`: guía verificada con índice, comandos de diagnóstico y causas que
  faltaban (cabeceras `python3-dev`, Python 3.13+, Docker Desktop en macOS/Windows sin
  `/dev/video0`, substream, `FACE_SCALE`, cooldowns de zona, etc.).
- `docs/PUBLICAR_EN_GITHUB.md`: publicación manual, con `gh`, con script y **prompt listo
  para el agente de opencode**.
- `scripts/configure_repo.py`: personaliza badges, autor, LICENSE y Docker Hub en un comando
  (`--check` para validar que no quedan placeholders).
- `scripts/publish_github.sh`: push seguro que **aborta** si detecta `.env`, `*.db`, `*.pt` o
  fotos de `known_faces/` en el índice de git.
- Tests nuevos: `tests/test_troubleshooting.py` (18) y `tests/test_scripts.py` (4) que
  blindan las afirmaciones de la documentación. Total: **111 tests**.

## [2.0.0] — 2026-10-26

### Añadido

- **SQLite** como historial permanente (`events`, `zones`, `people`) con WAL, filtros por
  cámara/hora/tipo, resúmenes agregados y retención (`cleanup_history`).
- **Zonas restringidas (polígonos de intrusión)** normalizadas 0..1, con tracker IoU,
  tiempo de permanencia, cooldown anti-spam y evidencia JPEG.
- **Soporte multicámara** (`CAMERAS` en JSON), un hilo por cámara con reconexión y backoff.
- **GitHub Actions**: `ci.yml` (lint + matriz Python × MCP SDK + smoke test Docker) y
  `docker-publish.yml` (multi-arch amd64/arm64 a Docker Hub con tags semánticos).
- **Docker**: Dockerfile multi-stage con dlib compilado, usuario no-root, healthcheck,
  volúmenes y `docker-compose.yml`.
- Paquete instalable `mcp_security_vision` con separación de responsabilidades
  (cámara, pipeline, detector, caras, zonas, tracker, base de datos, snapshots, overlay).
- 13 herramientas MCP + recurso `security://status`; compatible con MCP SDK **1.x y 2.x**.
- Webhook opcional de alertas, cámara sintética y modelos falsos para CI/demos.
- `scripts/check_setup.py`, `scripts/draw_zones.py` y `Makefile`.

### Corregido

- El import `from mcp.server.fastmcp import Image` no existe en SDK 2.x (el SDK se renombró
  a `MCPServer`); se añadió un *shim* compatible con ambas versiones.
- Condiciones de carrera al recargar el registro facial (ahora con `RLock`).
- Anti-spam de logs basado en el último evento (sustituido por cooldowns por clave).
- `register_current_face` guardaba el fotograma completo y no validaba nombre ni número de
  rostros.
- Contraseñas RTSP enmascaradas en logs y en `list_cameras`.

[2.1.0]: #210--2026-10-29
[2.0.1]: #201--2026-10-26
[2.0.0]: #200--2026-10-26
