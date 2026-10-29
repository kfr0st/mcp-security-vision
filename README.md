# 🛡️ MCP Security Vision

Servidor **[MCP (Model Context Protocol)](https://modelcontextprotocol.io)** para videovigilancia inteligente en tiempo real: conecta cámaras de seguridad (IP por RTSP, analógicas por DVR o capturadora USB) con agentes de IA como **Claude Desktop**, **Cursor**, **LangChain**, **AutoGen** o tus propios bots.

**YOLOv8** detecta objetos, **face_recognition** identifica personas conocidas y **polígonos de intrusión** vigilan zonas restringidas. Todo el historial se guarda en **SQLite** y se expone al agente como herramientas MCP.

![Captura anotada de demostración](docs/demo_annotated.jpg)

<sub>Imagen generada por `overlay.py` con la cámara sintética (`CAMERA_SOURCE=synthetic`): objetos YOLO, rostro identificado y dos zonas restringidas, una de ellas en alerta (rojo).</sub>

<!-- Badges personalizados para la cuenta de GitHub: kfr0st -->
[![CI](https://github.com/kfr0st/mcp-security-vision/actions/workflows/ci.yml/badge.svg)](https://github.com/kfr0st/mcp-security-vision/actions/workflows/ci.yml)
[![Docker Hub](https://img.shields.io/docker/v/kfr0st/mcp-security-vision?label=docker%20hub&logo=docker)](https://hub.docker.com/r/kfr0st/mcp-security-vision)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-1.x%20%7C%202.x-8A2BE2)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 📚 Índice

- [Características](#-características)
- [Novedades de la v2.0 / v2.1](#-novedades-de-la-v20--v21)
- [Arquitectura](#️-arquitectura)
- [Instalación](#-instalación)
- [Configuración de cámaras](#-configuración-de-cámaras)
- [Variables de entorno](#️-variables-de-entorno-principales)
- [Conexión con agentes de IA](#-conexión-con-agentes-de-ia)
- [Herramientas MCP](#-herramientas-mcp)
- [Zonas restringidas](#-zonas-restringidas-intrusión)
- [Base de datos SQLite](#️-base-de-datos-sqlite)
- [Docker](#-docker)
- [GitHub Actions y Docker Hub](#-github-actions-y-docker-hub)
- [Desarrollo y pruebas](#-desarrollo-y-pruebas)
- [Seguridad y privacidad](#-seguridad-y-privacidad)
- [Problemas frecuentes](#-problemas-frecuentes)
- [Roadmap](#️-roadmap)
- [Publicar en GitHub](#-publicar-en-github)
- [Contribuir](#-contribuir)
- [Licencia](#-licencia)

---

## ✨ Características

| | |
|---|---|
| 🎯 **Detección de objetos** | YOLOv8 (personas, autos, mascotas, mochilas, paquetes…) con confianza configurable |
| 🧑 **Reconocimiento facial** | Identifica personas conocidas (`known_faces/`) vs. desconocidos, con estabilización temporal anti-falsos positivos |
| 🚧 **Zonas restringidas** | Polígonos de intrusión normalizados (0..1), por cámara y por tipo de objeto, con tiempo de permanencia |
| 🗄️ **Historial permanente** | SQLite con eventos, zonas, personas y rutas de evidencia; consultas por fecha, cámara y tipo |
| 🎥 **Multicámara** | Varias cámaras definidas en JSON, cada una con su hilo, tracker y estado |
| 🧵 **Multihilo con reconexión** | Bucle de vídeo en segundo plano, backoff exponencial si se cae el RTSP, nunca bloquea MCP |
| 🖼️ **Capturas para agentes multimodales** | `capture_snapshot` devuelve una imagen JPEG (con cajas/zonas opcionales) |
| 🔔 **Notificaciones** | Webhook JSON opcional (n8n, Home Assistant, Slack, Telegram vía bot) para alertas |
| 🐳 **Docker listo** | Dockerfile multi-stage + docker-compose + publicación automática en Docker Hub |
| ✅ **CI con GitHub Actions** | Lint, 144 tests (matriz Python 3.10/3.11/3.12 × MCP SDK 1.x/2.x) y smoke test de la imagen |
| 🔒 **Controles de seguridad** | Credenciales solo en `.env`, contraseñas RTSP enmascaradas en logs, HTTP ligado a loopback con validación de `Host`/`Origin`, usuario no-root en Docker |

---

## 🆕 Novedades de la v2.0 / v2.1

**v2.1.0** es la revisión de seguridad y robustez: HTTP en loopback con validación de
`Host`/`Origin`, `cleanup_history` con confirmación, esquema SQLite v2, backoff real de
cámara, inferencia serializada y 144 tests. Ver [CHANGELOG.md](CHANGELOG.md).

**v2.0.1** corrige la guía de problemas frecuentes tras verificarla contra el código (mensajes de consola reales,
`RTSP_TRANSPORT`, `FACE_MODEL`, validación de `YOLO_IMGSZ`, comprobación de puerto antes de cargar modelos)
y añade los scripts de publicación. Ver [CHANGELOG.md](CHANGELOG.md).

La v2.0 incorporó las extensiones solicitadas:

1. **Base de datos SQLite para historial permanente** (`mcp_security_vision/database.py`)
   - Tablas `events`, `zones`, `people`, índices y WAL para escrituras concurrentes.
   - Herramientas `get_security_report`, filtros por horas/tipo y `cleanup_history` con retención.
2. **GitHub Actions para pruebas y publicación en Docker Hub**
   - `.github/workflows/ci.yml`: lint + tests + smoke test del contenedor.
   - `.github/workflows/docker-publish.yml`: build multi-arquitectura (amd64/arm64) y push con tags semánticos.
3. **Zonas restringidas (polígonos de intrusión)** (`mcp_security_vision/zones.py`)
   - Detección punto-en-polígono, tracker por IoU para no duplicar eventos, dwell time y evidencia fotográfica.
   - Herramientas MCP `add_restricted_zone`, `list_restricted_zones`, `remove_restricted_zone`, `get_active_intrusions`.

Y de la hoja de ruta v2.0 original:

4. **Soporte multicámara** con `CAMERAS` en JSON y `camera_id` en todas las herramientas.
5. **Arquitectura desacoplada** (paquete `mcp_security_vision/`), lista para combinarse con otros servidores MCP
   (Telegram, WhatsApp, Notion…) sin tocar el código de visión, más webhook opcional para reaccionar a alertas.

---

## 🏗️ Arquitectura

```text
                    ┌──────────────────────────────┐
   Claude Desktop   │                              │
   Cursor / Cline ──┤        Servidor MCP          │
   LangChain/AutoGen│  server.py  (13 tools + 1    │
   HTTP / Docker ───┤  recurso security://status)  │
                    └──────────────┬───────────────┘
                                   │  (mismo proceso, sin bloqueo)
                    ┌──────────────▼───────────────┐
                    │        VisionSystem          │
                    │  cámaras · zonas · registro  │
                    └──────┬─────────────┬─────────┘
        ┌──────────────────┼─────────────┼──────────────────┐
        ▼                  ▼             ▼                  ▼
 ┌────────────┐    ┌─────────────┐  ┌──────────┐    ┌────────────┐
 │ CameraWorker│   │  Pipeline   │  │  SQLite  │    │ Snapshots  │
 │ (1 hilo por │   │ YOLO → IoU  │  │ events   │    │  alertas   │
 │  cámara)    │   │ → caras →   │  │ zones    │    │  .jpg      │
 │ reconexión  │   │ zonas → log │  │ people   │    │ retención  │
 └────────────┘    └─────────────┘  └──────────┘    └────────────┘
        │                  │
        │                  └─────► Webhook JSON (opcional)
        └── RTSP · USB · DVR · synthetic
```

### Estructura del repositorio

```text
mcp-security-vision/
├── mcp_security_vision/          # Paquete instalable
│   ├── server.py                 # Herramientas MCP + arranque (compat. SDK 1.x/2.x)
│   ├── system.py                 # Fachada de negocio (lo que usan las tools)
│   ├── camera.py                 # Hilo por cámara + reconexión + estado
│   ├── pipeline.py               # Detección → tracking → caras → zonas → eventos
│   ├── detectors.py              # YOLOv8 + utilidades de cajas
│   ├── faces.py                  # Registro y reconocimiento facial
│   ├── zones.py                  # Polígonos de intrusión + monitor
│   ├── tracker.py                # Tracker IoU (IDs estables)
│   ├── database.py               # SQLite: eventos, zonas, personas
│   ├── snapshots.py              # Evidencias JPEG y retención
│   ├── overlay.py                # Dibujo de cajas, rostros y zonas
│   ├── notifications.py          # Webhook opcional en segundo plano
│   ├── synthetic.py              # Cámara sintética para demos/CI
│   ├── fakes.py                  # Modelos simulados (CI, sin torch ni dlib)
│   └── config.py                 # Settings desde .env (+ multicámara JSON)
├── config/zones.example.json     # Zonas iniciales (seed)
├── known_faces/                  # Fotos de personas conocidas (nombre_apellido.jpg)
├── snapshots/                    # Evidencias generadas
├── data/                         # security.db (SQLite)
├── docs/
│   ├── TROUBLESHOOTING.md        # Problemas frecuentes (verificados con tests)
│   ├── PUBLICAR_EN_GITHUB.md     # Cómo subir el repo (manual, gh, script, agente)
│   └── demo_annotated.jpg        # Captura anotada de ejemplo
├── scripts/
│   ├── check_setup.py            # Diagnóstico de instalación
│   ├── draw_zones.py             # Editor visual de polígonos
│   ├── configure_repo.py         # Quita placeholders antes de publicar
│   └── publish_github.sh         # Push seguro con verificación de secretos
├── tests/                        # 144 tests (unitarios + integración MCP)
├── .github/workflows/            # ci.yml + docker-publish.yml
├── .github/ISSUE_TEMPLATE/       # plantillas de bug y propuesta
├── SECURITY.md · CONTRIBUTING.md # política de seguridad y guía de contribución
├── server.py                     # Atajo: python server.py
├── Dockerfile · docker-compose.yml · Makefile
├── requirements.txt · requirements-dev.txt · pyproject.toml
└── .env.example
```

---

## 🚀 Instalación

### Requisitos

- Python **3.10, 3.11 o 3.12** (dlib todavía no soporta 3.13+)
- `cmake` y compilador C++ (para `dlib`, que usa `face-recognition`)
  - Debian/Ubuntu: `sudo apt install build-essential cmake libopenblas-dev liblapack-dev libgl1`
  - macOS: `brew install cmake`
  - Windows: *Visual Studio Build Tools* con la carga de trabajo **Desarrollo para el escritorio con C++** + CMake
- Opcional: GPU NVIDIA con CUDA para acelerar YOLO (`YOLO_DEVICE=0`)

### Pasos

```bash
git clone https://github.com/kfr0st/mcp-security-vision.git
cd mcp-security-vision

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env              # configura tus cámaras y rutas
python scripts/check_setup.py --read-frames
```

> **¿Sin cámara a mano?** Prueba todo el flujo con la cámara sintética y modelos simulados:
> ```bash
> VISION_FAKE_MODELS=1 CAMERA_SOURCE=synthetic python server.py --transport streamable-http --host 127.0.0.1
> ```
> O simplemente `make demo`.

### Instalación como paquete (opcional)

```bash
pip install ".[vision]"     # código + YOLO + dlib
mcp-security-vision --check # comando instalado
```

---

## 📷 Configuración de cámaras

Todo se define en `.env` (nunca en el código). Ver `.env.example` para la lista completa.

### Una sola cámara

```ini
# Webcam o capturadora USB (índice del dispositivo)
CAMERA_SOURCE=0

# Cámara IP / DVR por RTSP
# CAMERA_SOURCE=rtsp://usuario:clave@192.168.1.50:554/Streaming/Channels/101
```

### Varias cámaras

```ini
CAMERAS={"entrada": "rtsp://admin:clave@192.168.1.50:554/Streaming/Channels/101", "patio": 0, "demo": "synthetic"}

# O con nombres visibles y cámaras deshabilitadas:
# CAMERAS=[{"id":"entrada","name":"Puerta principal","source":"rtsp://..."},
#          {"id":"patio","name":"Patio trasero","source":0},
#          {"id":"cochera","name":"Cochera","source":2,"enabled":false}]
```

Todas las herramientas aceptan `camera_id`; si se omite se usa la primera cámara habilitada.

### URLs RTSP habituales

| Marca | URL del canal 1 (principal) | Substream (menos CPU) |
|---|---|---|
| Hikvision (cámara) | `rtsp://user:pass@IP:554/Streaming/Channels/101` | `.../102` |
| Hikvision (DVR/NVR) | `rtsp://user:pass@IP:554/Streaming/Channels/101` | `.../102` |
| Dahua | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` | `subtype=1` |
| TP-Link Tapo | `rtsp://user:pass@IP:554/stream1` | `stream2` |
| Reolink | `rtsp://user:pass@IP:554/h264Preview_01_main` | `_sub` |
| Amcrest | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` | `subtype=1` |

> 🎥 **Cámaras analógicas (coaxial/BNC)**: sin cambiar una línea de código.
> 1. **Vía DVR/XVR (recomendado)**: conecta el DVR al router y usa su RTSP por canal.
> 2. **Vía capturadora RCA/BNC → USB** (~10 USD): el sistema la ve como webcam `CAMERA_SOURCE=0/1`.

---

## ⚙️ Variables de entorno principales

| Variable | Por defecto | Descripción |
|---|---|---|
| `CAMERA_SOURCE` / `CAMERAS` | `0` | Fuente única o JSON multicámara (RTSP, índice USB, `synthetic`) |
| `PROCESS_EVERY_N_FRAMES` | `5` | Analiza IA cada N fotogramas (sube si la CPU va justa) |
| `YOLO_MODEL` | `yolov8n.pt` | `yolov8n/s/m/l/x.pt` (n = más rápido) |
| `YOLO_CONF` | `0.5` | Umbral de confianza de objetos |
| `YOLO_DEVICE` | *(auto)* | `cpu`, `0` (GPU CUDA), `mps` (Apple) |
| `FACE_TOLERANCE` | `0.6` | Distancia máxima para considerar la misma persona (menor = más estricto) |
| `FACE_MODEL` | `hog` | `hog` (rápido, CPU) o `cnn` (más preciso, GPU) |
| `FACE_SCALE` | `0.25` | Escala a la que se buscan rostros (súbela si las caras son pequeñas) |
| `FACE_STABILIZER_WINDOW` / `..._MIN_VOTES` | `3` / `2` | Anti-parpadeo del reconocimiento |
| `FACES_WITHOUT_PERSON` | `false` | Buscar rostros incluso sin detección de "person" |
| `EVENT_COOLDOWN_SECONDS` | `20` | Anti-spam del mismo evento |
| `INTRUSION_COOLDOWN_SECONDS` | `30` | Anti-spam de reentradas a una zona |
| `PRESENCE_TIMEOUT_SECONDS` | `2.5` | Tiempo sin ver a alguien para considerar que salió de la zona |
| `SNAPSHOT_ON_ALERT` | `true` | Guardar evidencia JPEG de cada alerta |
| `DB_PATH` | `data/security.db` | Base de datos SQLite |
| `KNOWN_FACES_DIR` | `known_faces` | Fotos de referencia |
| `SNAPSHOTS_DIR` | `snapshots` | Capturas |
| `ZONES_SEED_FILE` | `config/zones.example.json` | Zonas iniciales (solo se importan si no existen) |
| `RECONNECT_DELAY_SECONDS` / `MAX_...` | `5` / `60` | Backoff de reconexión RTSP |
| `RTSP_TRANSPORT` | `tcp` | `tcp` (estable), `udp` o `auto` (deja que FFmpeg decida) |
| `MCP_TRANSPORT` | `stdio` | `stdio`, `sse` o `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | Solo para transportes HTTP |
| `ALERT_WEBHOOK_URL` | *(vacío)* | Webhook JSON para alertas |
| `VISION_FAKE_MODELS` | `false` | Modo demo/CI sin YOLO ni dlib |

---

## 🤖 Conexión con agentes de IA

### Claude Desktop / Cursor (transporte stdio)

Edita `claude_desktop_config.json` (`~/Library/Application Support/Claude/` en macOS,
`%APPDATA%\Claude\` en Windows) y añade:

```json
{
  "mcpServers": {
    "security-camera": {
      "command": "/ruta/absoluta/a/mcp-security-vision/venv/bin/python",
      "args": ["/ruta/absoluta/a/mcp-security-vision/server.py"],
      "env": {
        "CAMERA_SOURCE": "rtsp://usuario:clave@192.168.1.50:554/Streaming/Channels/101"
      }
    }
  }
}
```

> En Windows usa `C:\\ruta\\...\\venv\\Scripts\\python.exe`.
> Los logs del servidor van a **stderr**, así que no rompen el protocolo stdio.

### Clientes HTTP (Docker, remoto, agentes web)

```bash
docker compose up -d --build          # endpoint: http://localhost:8000/mcp
# o en local (solo accesible desde tu máquina):
python server.py --transport streamable-http --host 127.0.0.1 --port 8000
```

Ejemplo mínimo con el SDK oficial de Python:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    async with streamablehttp_client("http://localhost:8000/mcp") as streams:
        read, write = streams[0], streams[1]  # 3 valores en SDK 1.x, 2 en SDK 2.x
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = await session.call_tool("get_live_status", {})
            print(status.content[0].text)


asyncio.run(main())
```

> El SDK 2.x renombró algunas funciones; el servidor incluye un *shim* para funcionar con **MCP SDK 1.x y 2.x** sin cambios.

### Prompts de ejemplo

- *«¿Hay alguien en la entrada ahora mismo? ¿Es alguien conocido?»*
- *«Toma una captura anotada del patio y dime si el repartidor dejó un paquete en el suelo.»*
- *«Revisa los registros de las últimas 24 horas y dime a qué hora pasó Carlos.»*
- *«Define una zona restringida llamada "Caja fuerte" con el polígono [[0.6,0.4],[0.95,0.4],[0.95,0.9],[0.6,0.9]] en cam1.»*
- *«¿Hay alguna intrusión activa en este momento?»*
- *«Estoy frente a la cámara: regístrame como "Administrador".»*

---

## 🧰 Herramientas MCP

| Herramienta | Parámetros | Devuelve |
|---|---|---|
| `list_cameras` | — | Cámaras, fuente enmascarada, estado y FPS |
| `get_live_status` | `camera_id=""` | Objetos, rostros, zonas con intrusos y últimos eventos |
| `capture_snapshot` | `camera_id=""`, `annotate=false`, `save=false` | **Imagen JPEG** (con cajas y zonas si `annotate=true`); `save=true` la guarda además en `snapshots/` |
| `get_security_logs` | `limit=20`, `camera_id=""`, `hours=0`, `event_type=""` | Historial SQLite filtrado |
| `get_security_report` | `hours=24`, `camera_id=""` | Totales por tipo/cámara, rostros y zonas |
| `get_active_intrusions` | `camera_id=""` | Personas/objetos dentro de zonas ahora |
| `list_known_people` | — | Personas registradas (foto + alta) |
| `register_current_face` | `person_name`, `camera_id=""` | Guarda el rostro actual (exige 1 sola persona) |
| `forget_known_person` | `person_name` | Elimina foto, memoria y fila SQLite |
| `list_restricted_zones` | `camera_id=""` | Polígonos, etiquetas y estado |
| `add_restricted_zone` | `camera_id`, `name`, `points`, `labels=["person"]`, `enabled=true` | Crea/actualiza zona |
| `remove_restricted_zone` | `camera_id`, `name` | Elimina zona |
| `cleanup_history` | `keep_days=30`, `purge_snapshots=false`, `confirm=false` | Purga eventos antiguos (y capturas si `purge_snapshots=true`); exige `confirm=true` |
| *(recurso)* `security://status` | — | JSON con cámaras, personas, zonas y últimos eventos |

### Eventos registrados (`event_type`)

`intrusion` · `face_known` · `face_unknown` · `person_unidentified` · `camera_online` · `camera_offline` ·
`face_registered` · `face_forgotten` · `zone_added` · `zone_removed` · `system`

---

## 🚧 Zonas restringidas (intrusión)

Las coordenadas son **normalizadas** (0..1) respecto al ancho y alto de la imagen:
`(0,0)` = esquina superior izquierda, `(1,1)` = inferior derecha. Así la zona funciona con 720p, 1080p o 4K
y sobrevive a reconexiones.

```json
[
  [0.35, 0.30],
  [0.65, 0.30],
  [0.65, 0.95],
  [0.35, 0.95]
]
```

**Cómo crear zonas**

1. **Pidiéndoselo al agente** (herramienta `add_restricted_zone`).
2. **Con el editor visual** (recomendado para ajustar a mano):
   ```bash
   python scripts/draw_zones.py --camera-id cam1 --name "Puerta trasera" --labels person,car
   # clic = vértice · u = deshacer · c = limpiar · s = guardar
   ```
3. **Con el archivo semilla** `config/zones.example.json` (se importa al arrancar si la zona no existe).

**Mecánica anti-spam**

- El tracker asigna un ID estable (`#1`, `#2`…) a cada objeto; solo se registra **una intrusión por entrada**.
- Al salir de la zona (sin detección durante `PRESENCE_TIMEOUT_SECONDS`) se libera el estado, y una nueva entrada
  vuelve a alertar (respetando `INTRUSION_COOLDOWN_SECONDS`).
- Cada intrusión guarda una captura de evidencia y se acompaña de `dwell_seconds` (tiempo dentro).

---

## 🗄️ Base de datos SQLite

Archivo: `data/security.db` (WAL activado para escrituras concurrentes desde varios hilos de cámara).

```sql
-- Esquema resumido
events(id, camera_id, created_at, event_type, severity, description,
       object_label, track_id, zone_names, snapshot_path, metadata)
zones(id, camera_id, name, points, labels, enabled, created_at, updated_at)
people(id, name, slug, file_name, created_at, updated_at)
```

Consultas útiles:

```sql
-- ¿Quién pasó hoy?
SELECT created_at, description FROM events
WHERE event_type = 'face_known' AND created_at >= date('now', 'start of day');

-- Intrusiones por zona en los últimos 7 días
SELECT zone_names, COUNT(*) FROM events
WHERE event_type = 'intrusion' AND created_at >= datetime('now', '-7 days')
GROUP BY zone_names;

-- Agenda de presencia por persona
SELECT json_extract(metadata, '$.person') AS persona, MIN(created_at), MAX(created_at), COUNT(*)
FROM events WHERE event_type = 'face_known' GROUP BY persona;
```

Las marcas de tiempo son **hora local del servidor** en ISO-8601 (`YYYY-MM-DDTHH:MM:SS`), ordenables como texto.

**Respaldo**: copia `data/security.db` (con WAL, usa `sqlite3 data/security.db ".backup copia.db"`).
**Retención**: `cleanup_history(keep_days=90, confirm=true)`; añade `purge_snapshots=true`
para borrar también las capturas. Sin `confirm=true` la herramienta solo describe lo que haría.

---

## 🐳 Docker

```bash
# 1. Configura el entorno
cp .env.example .env

# 2. Construye y levanta (endpoint MCP en http://localhost:8000/mcp)
docker compose up -d --build
docker compose logs -f

# 3. Prueba rápida sin cámara (modo demo)
docker run --rm -p 127.0.0.1:8000:8000 -e VISION_FAKE_MODELS=1 -e CAMERA_SOURCE=synthetic \
  kfr0st/mcp-security-vision:latest
```

- Imagen **multi-stage** (dlib se compila en el *builder*; la imagen final no lleva compiladores).
- Corre como **usuario no-root** (`vision`).
- Volúmenes: `data` (SQLite), `snapshots`, `known_faces`, `config`, `yolo-models` (pesos persistentes).
- Para cámara USB en Linux añade `devices: ["/dev/video0:/dev/video0"]` y `group_add: ["video"]`.
- Por defecto solo se aceptan hosts locales (`localhost`, `127.0.0.1`, `[::1]` y sus puertos).
  Si publicas detrás de un proxy, añade tu dominio a `MCP_ALLOWED_HOSTS` (por ejemplo,
  `MCP_ALLOWED_HOSTS=mcp.midominio.com,mcp.midominio.com:*`) y define
  `MCP_ALLOWED_ORIGINS=https://mcp.midominio.com` si usas un navegador.
- El transporte HTTP **no trae autenticación**. No lo expongas directamente a Internet:
  usa un proxy con TLS y autenticación, o una red privada/VPN.
- `PRELOAD_MODELS=true` (build-arg) incluye `yolov8n.pt` en la imagen para despliegues sin Internet.
- El contenedor corre como UID 1000. Si usas *bind mounts* en Linux y ves `Permission denied`:
  `sudo chown -R 1000:1000 data snapshots known_faces` (o usa volúmenes nombrados).

---

## 🔁 GitHub Actions y Docker Hub

### CI (`.github/workflows/ci.yml`)

Se ejecuta en cada push y pull request:

1. **Lint**: `ruff check` + `ruff format --check`.
2. **Tests**: matriz Python **3.10 / 3.11 / 3.12** × **MCP SDK 1.x / 2.x**, con `VISION_FAKE_MODELS=1`
   (sin torch ni dlib → CI en ~1 min), cobertura y JUnit XML.
3. **Smoke test Docker**: construye la imagen, arranca el contenedor en modo demo y espera a que abra el puerto 8000.

### Publicación en Docker Hub (`.github/workflows/docker-publish.yml`)

Se dispara con tags `v*.*.*`, pushes a `main` o manualmente. Construye `linux/amd64` + `linux/arm64` y publica:

| Evento | Tags |
|---|---|
| Tag `v1.2.3` | `1.2.3`, `1.2`, `1`, `latest` |
| Push a `main` | `main`, `sha-xxxxxxx`, `latest` |

**Configuración (una sola vez)**

1. Crea un repositorio en Docker Hub: `kfr0st/mcp-security-vision`.
2. Genera un **Access Token** en Docker Hub → *Account settings → Security*.
3. En GitHub → *Settings → Secrets and variables → Actions*, añade:
   - `DOCKERHUB_USERNAME`
   - `DOCKERHUB_TOKEN`
   - *(opcional)* `DOCKERHUB_REPO` si el repo no se llama `mcp-security-vision`.
4. Publica:
   ```bash
   git tag v2.1.0 && git push origin v2.1.0
   ```

> Si faltan los secrets, el workflow solo hace build (con un *warning*) y no falla.

---

## 🧪 Desarrollo y pruebas

```bash
pip install -r requirements-dev.txt      # sin torch/dlib
pip install --no-deps -e .

make test      # pytest + cobertura (144 tests, ~25 s)
make lint      # ruff check
make format    # ruff format + fixes
make demo      # servidor con cámara sintética y modelos falsos
make check     # valida .env y rutas
```

Qué cubren los tests:

- Parseo de configuración (multicámara, validaciones, enmascarado de credenciales).
- SQLite: eventos, filtros temporales, resúmenes, zonas, personas y persistencia.
- Geometría de polígonos + monitor de intrusiones (entrada, dwell, reentrada, etiquetas).
- Tracker IoU, estabilizador de nombres, registro/reconocimiento facial (con backend falso).
- Pipeline completo con detectores simulados y evidencias JPEG.
- **Webhook** de notificaciones contra un servidor HTTP local.
- **Integración MCP real**: levanta `server.py` en un subproceso, lista herramientas, llama a
  `get_live_status`, `add_restricted_zone`, `get_security_logs`, `capture_snapshot` (verifica JPEG) y el recurso `security://status`.

> `server.py` se ejecuta en un subproceso durante los tests, por eso la cobertura global (~81 %) es menor que
> la de los módulos de lógica (~90 %); el arranque está cubierto end-to-end por el test de integración.

---

## 🔒 Seguridad y privacidad

- Las contraseñas de cámaras viven **solo** en `.env` (ignorado por git) o en variables de entorno.
- Los logs muestran las URLs con la clave y los tokens de la query enmascarados (`rtsp://admin:***@...`).
- `known_faces/`, `snapshots/` y `data/` están en `.gitignore`: **no subas fotos de personas** al repo.
- Recomendado: usuarios RTSP de solo lectura y contraseñas fuertes; segmenta las cámaras en una VLAN.
- El reconocimiento facial biométrico es dato sensible: informa a las personas y cumple tu normativa local
  (GDPR/RGPD, LFPDPPP, Ley 29733 en Perú, etc.).
- En Docker el proceso corre sin privilegios y con `no-new-privileges`.
- El transporte HTTP escucha en loopback y valida `Host`/`Origin` por defecto, pero **no incluye autenticación**:
  si lo expones, ponlo detrás de HTTPS + autenticación y limita `MCP_ALLOWED_HOSTS`.
- `cleanup_history` exige `confirm=true` y no borra capturas salvo que pidas `purge_snapshots=true`.

### Checklist para producción

- [ ] `MCP_HOST=127.0.0.1` o proxy con TLS y autenticación.
- [ ] `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` ajustados a tu dominio real.
- [ ] `.env` fuera de git y con permisos restringidos (`chmod 600`).
- [ ] Usuario RTSP de solo lectura y cámaras en una VLAN segmentada.
- [ ] Retención definida (`cleanup_history`) y copias de seguridad de `data/security.db`.
- [ ] Aviso de privacidad y base legal para las personas grabadas.
- [ ] Contenedor como usuario no-root y volúmenes con permisos mínimos.
- [ ] Dependencias auditadas (`pip-audit`, Dependabot) y tokens rotados.

---

## 🩺 Problemas frecuentes

> Guía completa y **verificada ejecutando el código**: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).
> Cada fila está cubierta por tests (`tests/test_troubleshooting.py`), así que no se queda desfasada.

| Síntoma | Causa / solución verificada |
|---|---|
| `pip install face-recognition` falla compilando dlib | `face-recognition` no tiene binarios: **dlib se compila**. Instala `build-essential cmake python3-dev libopenblas-dev liblapack-dev` (el paquete **`python3-dev`** es imprescindible y casi siempre se olvida). Windows: *Build Tools C++* + CMake. Usa **Python 3.10–3.12** (dlib falla en 3.13+). Alternativa sin compilar: imagen Docker o `VISION_FAKE_MODELS=1` |
| La cámara entra en bucle de «sin señal» | El mensaje real es `[cam1] Cámara SIN SEÑAL: … Reintentando en 5s...` (no `[WARN] Señal de cámara perdida`). Comprueba URL/credenciales con `ffplay -rtsp_transport tcp <url>`, usa el **substream** (`/102`, `stream2`, `_sub`), revisa el canal del DVR y el códec (H.264 básico). El transporte es **TCP por defecto** (`RTSP_TRANSPORT`); si tu equipo necesita UDP: `RTSP_TRANSPORT=udp`. Diagnóstico: `python scripts/check_setup.py --read-frames` |
| La imagen va a tirones / con retraso | El análisis corre en el hilo de la cámara. Orden de impacto: `FACES_ENABLED=false` → `FACE_SCALE=0.15` → `PROCESS_EVERY_N_FRAMES=10` → `YOLO_IMGSZ=480` (debe ser **múltiplo de 32**, se valida al arrancar) → substream → `YOLO_DEVICE=0` (GPU) |
| No reconoce a nadie / dice «Desconocido» | Foto reciente, de frente, iluminada y con **un solo rostro**. `FACE_TOLERANCE=0.65` ayuda, pero también: cara pequeña → `FACE_SCALE=0.5`; pocas lecturas → `FACE_STABILIZER_MIN_VOTES=1`; YOLO no ve «person» → `FACES_WITHOUT_PERSON=true`; `FACE_MODEL=cnn` (más preciso, lento en CPU). `list_known_people` avisa si una foto no tiene rostro detectable |
| Falsos positivos de personas | `YOLO_CONF=0.6` y `FACE_STABILIZER_MIN_VOTES=3` con `FACE_STABILIZER_WINDOW=5` (si `MIN_VOTES > WINDOW` el arranque falla con `ConfigError`). **No** subas `FACE_TOLERANCE` en ese caso |
| `Address already in use` | Solo ocurre con `sse`/`streamable-http` (con `stdio` no hay puerto). Desde v2.0.1 el servidor **comprueba el puerto antes de cargar los modelos** y muestra cómo liberarlo o cambiarlo (`MCP_PORT=8001`) |
| Claude Desktop no conecta | Ruta **absoluta** al python del venv (`.../venv/bin/python` o `...\venv\Scripts\python.exe`). Logs: macOS `~/Library/Logs/Claude/mcp.log` y `mcp-server-security-camera.log`; Windows `%APPDATA%\Claude\logs\`. Prueba antes `python server.py --check` y reinicia la app por completo |
| El contenedor no ve `/dev/video0` | Añade `devices: ["/dev/video0:/dev/video0"]` + `group_add: ["video"]`. **Solo funciona en Linux**: Docker Desktop en macOS/Windows no puede pasar dispositivos USB (usa RTSP) |
| La zona nunca dispara | El punto de referencia es el **centro inferior de la caja (los pies)**, no la cabeza. Además: coordenadas **0..1**, `labels=["person"]` (etiqueta exacta de YOLO, no «Persona»), zona `enabled`, `camera_id` correcto y respetar el cooldown de 30 s entre entradas. Verifica con `capture_snapshot(annotate=true)` |

<details>
<summary>Otros avisos de consola y qué significan</summary>

| Mensaje | Significado |
|---|---|
| `[cam1] Primer fotograma recibido: 1920x1080 (...)` | La cámara entrega vídeo ✅ |
| `[cam1] Cámara RECONECTADA (...)` | Se recuperó de una caída |
| `ConfigError: YOLO_IMGSZ debe ser múltiplo de 32` | Resolución de inferencia inválida (usa 320/480/512/640) |
| `SDK sin soporte de transport_security` | Actualiza `mcp` a ≥1.9 |
| `⚠️ face_recognition no está instalado` | Falta dlib: instala `requirements.txt` o usa el modo demo |

</details>

---

## 🗺️ Roadmap

- [ ] **Tracking con ByteTrack/BoTSORT** para oclusiones y multitudes.
- [ ] **Embeddings en SQLite** (evitar re-calcular rostros al arrancar) y cifrado en reposo.
- [ ] **Panel web** (FastAPI + HTMX) con stream anotado y mapa de zonas.
- [ ] **Notificaciones nativas** a Telegram/WhatsApp como MCP aparte o plugin.
- [ ] **Zonas por horario** («solo de 22:00 a 06:00») y reglas de dirección (entrar vs. salir).
- [ ] **Exportación** de clips MP4 alrededor de cada alerta.
- [ ] **Autenticación OAuth** para el transporte HTTP.

---

## 📤 Publicar en GitHub

Guía paso a paso (manual, con `gh`, con script y **prompt listo para el agente de opencode**):
[`docs/PUBLICAR_EN_GITHUB.md`](docs/PUBLICAR_EN_GITHUB.md)

```bash
# 1) personaliza badges/URLs/autor con tus datos
python scripts/configure_repo.py --github-user kfr0st --name "kfr0st"

# 2) publica (aborta si detecta .env, *.db, *.pt o fotos de known_faces/)
bash scripts/publish_github.sh git@github.com:kfr0st/mcp-security-vision.git
```

---

## 🤝 Contribuir

Lee [CONTRIBUTING.md](CONTRIBUTING.md) para el entorno de desarrollo, estilo y
comprobaciones antes del PR. Para vulnerabilidades usa el reporte privado de
[SECURITY.md](SECURITY.md); no abras un issue público.

1. Haz fork y crea una rama: `git checkout -b feature/mi-mejora`.
2. `make lint && make test` en verde.
3. Pull request describiendo el cambio y, si aplica, nuevos tests.

## 📄 Licencia

MIT © 2026 kfr0st. Ver [LICENSE](LICENSE).

---

<p align="center">
  Hecho con ❤️ para la comunidad MCP hispanohablante · <a href="https://modelcontextprotocol.io">modelcontextprotocol.io</a>
</p>
