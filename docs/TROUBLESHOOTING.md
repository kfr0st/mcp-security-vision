# 🩺 Problemas frecuentes — guía verificada

> **Estado:** cada fila de esta guía se comprobó ejecutando el código de la **v2.1.0**
> (no solo leyéndolo). Los tests que blindan estas afirmaciones están en
> [`tests/test_troubleshooting.py`](../tests/test_troubleshooting.py) y en
> [`tests/test_config.py`](../tests/test_config.py): si el código cambia y la guía
> deja de ser cierta, la CI falla.
>
> Comprobación rápida del entorno antes de tocar nada:
> ```bash
> python scripts/check_setup.py --read-frames
> ```

---

## Índice

1. [`pip install face-recognition` falla compilando dlib](#1-pip-install-face-recognition-falla-compilando-dlib)
2. [La cámara entra en bucle de «sin señal»](#2-la-cámara-entra-en-bucle-de-sin-señal)
3. [La imagen va a tirones / el vídeo va con retraso](#3-la-imagen-va-a-tirones--el-vídeo-va-con-retraso)
4. [No reconoce a nadie (o dice «Desconocido»)](#4-no-reconoce-a-nadie-o-dice-desconocido)
5. [Falsos positivos de personas](#5-falsos-positivos-de-personas)
6. [`Address already in use` / puerto ocupado](#6-address-already-in-use--puerto-ocupado)
7. [Claude Desktop (o Cursor) no conecta](#7-claude-desktop-o-cursor-no-conecta)
8. [El contenedor no ve `/dev/video0` (cámara USB)](#8-el-contenedor-no-ve-devvideo0-cámara-usb)
9. [La zona restringida nunca dispara](#9-la-zona-restringida-nunca-dispara)
10. [Otros avisos que verás en consola](#10-otros-avisos-que-verás-en-consola)

---

## 1. `pip install face-recognition` falla compilando dlib

**Qué pasa realmente:** `face-recognition` no publica binarios; su dependencia
**`dlib` se compila desde código C++** al instalarse. De ahí los errores
`CMake must be installed`, `Could not build wheels for dlib` o la compilación
eterna que termina en `error: subprocess-exited-with-error`.

**Causas y solución**

| Causa | Solución |
|---|---|
| Falta CMake o compilador | Debian/Ubuntu: `sudo apt install build-essential cmake python3-dev libopenblas-dev liblapack-dev`<br>macOS: `xcode-select --install && brew install cmake`<br>Windows: *Visual Studio Build Tools* → carga **«Desarrollo para el escritorio con C++»** + CMake |
| **Faltan las cabeceras de Python** (`Python.h`) | Añadir **`python3-dev`** (o `python3.x-dev` según tu versión). Es la causa que más se olvida y **no** aparecía en la lista original |
| Python demasiado nuevo | `dlib`/`face_recognition` dan problemas en **Python 3.13+**. Usa **Python 3.10, 3.11 o 3.12** (la imagen Docker del repo usa 3.11) |
| CMake instalado pero no en el PATH | Reinicia la terminal; verifica `cmake --version` |
| No quieres compilar nada | Usa la imagen publicada: `docker run ... kfr0st/mcp-security-vision` (dlib ya viene compilado) o el modo demo `VISION_FAKE_MODELS=1` |

**Cómo comprobar que quedó bien**

```bash
python -c "import dlib, face_recognition; print('dlib', dlib.__version__)"
# Si falla, prueba primero solo dlib para ver el error real:
pip install dlib --verbose
```

> El modo demo no necesita dlib ni torch: `VISION_FAKE_MODELS=1 CAMERA_SOURCE=synthetic python server.py`

---

## 2. La cámara entra en bucle de «sin señal»

**Corrección importante:** el mensaje `[WARN] Señal de cámara perdida` **no existe**
en el código. Los mensajes reales (v2.1.0) son:

```text
[cam1] Cámara SIN SEÑAL: no se pudo abrir la fuente de vídeo. Reintentando en 5s...
[cam1] Cámara SIN SEÑAL: señal de vídeo perdida o fin del stream. Reintentando en 5s...
[cam1] Error leyendo el stream: <detalle del backend>
[cam1] Primer fotograma recibido: 1920x1080 (rtsp://admin:***@192.168.1.50:554/...)
```

Si ves el aviso en bucle, revisa en este orden:

1. **URL y credenciales**: usuario, contraseña y puerto (554 por defecto). La
   contraseña se muestra enmascarada en los logs, así que un error 401 no se
   «ve»: pruébala aparte.
   ```bash
   ffplay -rtsp_transport tcp "rtsp://usuario:clave@192.168.1.50:554/Streaming/Channels/101"
   ```
2. **Transporte**: UDP pierde paquetes con muchas cámaras. El proyecto fuerza
   **TCP por defecto** (`RTSP_TRANSPORT=tcp`). Si tu equipo solo funciona con UDP:
   `RTSP_TRANSPORT=udp`.
3. **Códec / perfil**: usa el **substream** (H.264 básico) en lugar del mainstream
   H.265/4K: Hikvision `/102`, Dahua `subtype=1`, Tapo `stream2`, Reolink `_sub`.
4. **Ruta del canal** (DVR/NVR): canal 1 suele ser `.../Channels/101`, canal 2
   `.../Channels/201`, etc. Consulta el manual del DVR.
5. **Red**: que el servidor tenga ruta a la IP (`ping`), que no haya VLAN bloqueando
   el 554 y que la cámara no tenga límite de conexiones simultáneas.
6. **Comprobación integrada**:
   ```bash
   python scripts/check_setup.py --read-frames
   ```
   Intenta abrir cada cámara y te dice si entrega fotogramas y de qué resolución.

> El sistema reintenta con **backoff exponencial** (`RECONNECT_DELAY_SECONDS` → doble
> hasta `MAX_RECONNECT_DELAY_SECONDS`) y registra el evento `camera_offline` en SQLite
> como máximo una vez por minuto para no inundar el historial.

---

## 3. La imagen va a tirones / el vídeo va con retraso

**Síntoma medible:** en `get_live_status` verás pocos `fps` y/o `frames_analyzed`
que crece muy despacio. El análisis corre **en el mismo hilo** que la lectura de la
cámara, así que si la IA tarda más que el intervalo entre fotogramas, el stream se
retrasa.

Palancas, **de mayor a menor efecto** (verificadas contra el código):

| Ajuste | Efecto | Nota |
|---|---|---|
| `FACES_ENABLED=false` | El mayor ahorro en CPU (dlib es caro) | Pierdes identificación, mantienes YOLO y zonas |
| `FACE_SCALE=0.15` | Reduce el coste de dlib ~4× respecto a 0.25 | Rostros más pequeños dejan de detectarse |
| `PROCESS_EVERY_N_FRAMES=10` | Analiza 1 de cada 10 fotogramas | No acelera la lectura, reduce la carga |
| `YOLO_IMGSZ=480` | Menos cómputo en YOLO | **Debe ser múltiplo de 32** (480, 512…); ahora se valida al arrancar |
| `YOLO_MODEL=yolov8n.pt` | El más rápido | `yolov8s/m` son más precisos y mucho más lentos en CPU |
| Substream de la cámara | Menos píxeles que decodificar y procesar | La mejor relación esfuerzo/beneficio |
| `YOLO_DEVICE=0` | GPU NVIDIA (10-30× más rápido) | Requiere `torch` con CUDA instalado (`YOLO_DEVICE=mps` en Apple Silicon) |

Comprobación de que la GPU se usa de verdad:

```bash
python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

> El ahorro de CPU también depende del decodificador: `cv2.CAP_PROP_BUFFERSIZE=1`
> ya se fija para no acumular fotogramas viejos.

---

## 4. No reconoce a nadie (o dice «Desconocido»)

**Correcto:** foto reciente, de frente, bien iluminada y **un solo rostro**.
`FACE_TOLERANCE=0.6` → `0.65` hace el matching **más permisivo** (acepta rostros más
distintos; no lo subas por encima de ~0.68 o empezarán los falsos positivos).

**Causas que faltaban en la lista original:**

| Causa | Solución |
|---|---|
| La cara es **pequeña en la imagen** (persona lejos, cámara 4K) | `FACE_SCALE=0.5` (analiza a media resolución; más CPU) |
| El nombre necesita varias lecturas para confirmarse | `FACE_STABILIZER_MIN_VOTES=1` (o baja `FACE_STABILIZER_WINDOW`) |
| YOLO no ve «person» en ese frame y por eso no se buscan caras | `FACES_WITHOUT_PERSON=true` |
| La foto se cargó pero **sin rostro detectable** | `list_known_people` marca «⚠️ foto sin rostro detectado»; cambia la foto |
| Ángulo/iluminación difíciles | `FACE_MODEL=cnn` (mucho más preciso, lento en CPU; ideal con GPU) |
| Fotos con dos personas en el mismo archivo | Una foto = una persona (se usa el primer rostro) |

**Comprobación rápida:**

```bash
python - <<'PY'
import face_recognition, pathlib
for foto in pathlib.Path("known_faces").glob("*.*"):
    caras = face_recognition.face_encodings(face_recognition.load_image_file(str(foto)))
    print(f"{foto.name}: {len(caras)} rostro(s)")
PY
```

---

## 5. Falsos positivos de personas

**Correcto, y la fila original estaba incompleta:**

- Sube `YOLO_CONF` de `0.5` a `0.6` (o `0.65`).
- Sube `FACE_STABILIZER_MIN_VOTES` a **3** y `FACE_STABILIZER_WINDOW` a **5**.
  ⚠️ `MIN_VOTES` **no puede ser mayor** que `WINDOW`: el arranque falla con
  `ConfigError` (protección añadida).
- **No** subas `FACE_TOLERANCE` si el problema son confusiones entre personas.
- Para spam de eventos, sube `EVENT_COOLDOWN_SECONDS` (20 s por defecto) y
  `INTRUSION_COOLDOWN_SECONDS` (30 s).

---

## 6. `Address already in use` / puerto ocupado

**Correcto en esencia, con dos matices:**

1. Solo puede ocurrir con transporte HTTP (`sse` o `streamable-http`). Con **`stdio`**
   (Claude Desktop, Cursor) **no hay puerto**, así que ese error viene de otro proceso.
2. Desde la v2.0.1 el servidor comprueba el puerto **antes** de cargar los modelos y
   muestra un mensaje accionable en lugar de la traza de uvicorn:

   ```text
   [ERROR] El puerto 8000 ya está en uso en 127.0.0.1.
     · Cambia el puerto:   MCP_PORT=8001 python server.py --transport streamable-http
     · O libera el puerto:
         Linux/macOS: lsof -i :8000   (o)  ss -ltnp | grep 8000
         Windows:     netstat -ano | findstr :8000   y luego  taskkill /PID <PID> /F
     · Si usas Docker:    docker ps  y para el contenedor que lo ocupa.
   ```

---

## 7. Claude Desktop (o Cursor) no conecta

**Correcto:** usa la **ruta absoluta** al python del entorno virtual.

- macOS/Linux: `/ruta/absoluta/mcp-security-vision/venv/bin/python`
- Windows: `C:\\ruta\\absoluta\\mcp-security-vision\\venv\\Scripts\\python.exe`

**Logs (rutas oficiales, verificadas):**

| Sistema | General | Por servidor (stderr) |
|---|---|---|
| macOS | `~/Library/Logs/Claude/mcp.log` | `~/Library/Logs/Claude/mcp-server-security-camera.log` |
| Windows | `%APPDATA%\Claude\logs\mcp.log` | `%APPDATA%\Claude\logs\mcp-server-security-camera.log` |

```bash
# macOS: seguir en vivo
tail -n 20 -F ~/Library/Logs/Claude/mcp*.log
# Windows (PowerShell)
type "$env:AppData\Claude\logs\mcp*.log"
```

**Lista de comprobación**

1. El JSON de `claude_desktop_config.json` es válido (sin comas finales) y usa rutas absolutas.
2. Prueba el servidor a mano — si arranca y responde, el problema es la config:
   ```bash
   /ruta/venv/bin/python server.py --check
   ```
3. `YOLO_MODEL` descarga pesos la primera vez: el primer arranque puede tardar. Si no
   hay Internet, ejecuta una vez con red o usa `PRELOAD_MODELS=true` en Docker.
4. Cierra **por completo** Claude Desktop (icono de la barra de menús → *Quit*) y vuelve a abrirlo.
5. Logs: si `mcp-server-security-camera.log` no existe, Claude ni siquiera intentó
   arrancarlo (revisa la config). Si existe y muere, ahí está el error real (`ModuleNotFoundError`, ruta inválida…).
6. Cursor/Claude Code usan el mismo formato de `mcpServers`.

---

## 8. El contenedor no ve `/dev/video0` (cámara USB)

**Correcto, con un matiz importante:** el paso de dispositivos USB solo funciona en
**host Linux**. Docker Desktop en **macOS o Windows no puede** pasar `/dev/video0`;
en esos casos usa una cámara IP (RTSP) o ejecuta el servidor fuera de Docker.

```yaml
services:
  security-vision:
    devices:
      - /dev/video0:/dev/video0
    group_add:
      - video          # grupo con permiso sobre /dev/video*
```

En algunos sistemas hay que añadir la regla de cgroup o usar SELinux en permisivo:

```bash
docker run --rm -it --device=/dev/video0 \
  --device-cgroup-rule='c 81:* rmw' \
  --group-add video kfr0st/mcp-security-vision:latest
```

Comprobaciones:

```bash
ls -l /dev/video*                       # ¿existe en el host?
docker compose exec security-vision ls -l /dev/video*   # ¿existe en el contenedor?
v4l2-ctl --list-devices                 # (paquete v4l-utils)
```

> Recuerda poner `CAMERA_SOURCE=0` (o el índice correcto) dentro del contenedor.

---

## 9. La zona restringida nunca dispara

**Aquí la fila original era imprecisa:** no basta con que «la persona esté dentro».
El punto de referencia es el **centro inferior de la caja del objeto** (los **pies**
de la persona), no su cabeza ni su centro. Un polígono de suelo funciona; uno dibujado
sobre las cabezas no disparará nunca.

Lista de comprobación (todo verificado):

1. **Coordenadas normalizadas 0..1**, no píxeles. `[[0,0],[500,0],[500,500]]` es
   inválido → error explicativo (`VisionError`).
   Para convertir: `x_norm = x_px / ancho_imagen`, `y_norm = y_px / alto_imagen`.
2. **Los pies dentro del polígono.** Usa `capture_snapshot(annotate=true)` para ver el
   polígono dibujado sobre la imagen real.
3. **Etiqueta exacta de YOLO**: `labels=["person"]`, no `"Persona"` ni `"gente"`.
4. **Zona habilitada** (`enabled=true`): `list_restricted_zones` lo indica.
5. **`camera_id` correcto**: una zona pertenece a una cámara concreta (`cam1`, `entrada`…).
   Si la creaste con el id equivocado no se evalúa.
6. **La detección debe ocurrir**: si `YOLO_CONF` es muy alto o el substream tiene poca
   resolución, la persona puede no detectarse.
7. **Cooldowns**: una entrada se registra **una vez**; si sales y vuelves antes de
   `INTRUSION_COOLDOWN_SECONDS` (30 s) no habrá evento nuevo.
8. **Semilla JSON**: `config/zones.example.json` solo se importa al arrancar y **no
   sobrescribe** zonas existentes; edítala en SQLite o con las herramientas MCP.

Prueba de humo con la cámara sintética (sin hardware):

```bash
VISION_FAKE_MODELS=1 CAMERA_SOURCE=synthetic MCP_TRANSPORT=streamable-http python server.py
# y desde el agente:
#   add_restricted_zone  cam1  "Prueba"  [[0.30,0.20],[0.70,0.20],[0.70,0.95],[0.30,0.95]]
#   get_security_logs    {"event_type": "intrusion"}
```

---

## 10. Otros avisos que verás en consola

| Mensaje | Significado | Acción |
|---|---|---|
| `[cam1] Primer fotograma recibido: 1920x1080 (...)` | La cámara ya entrega vídeo | Ninguna |
| `[cam1] Cámara RECONECTADA (...)` | Se recuperó tras una caída | Ninguna (queda en SQLite como `camera_online`) |
| `VISION_FAKE_MODELS activo: se usan modelos simulados` | Modo demo/CI | Ponlo a `false` en producción |
| `SDK sin soporte de transport_security` | SDK MCP antiguo | Actualiza `mcp` a ≥1.9 |
| `ConfigError: YOLO_IMGSZ debe ser múltiplo de 32` | Resolución de inferencia inválida | Usa 320, 480, 512 o 640 |
| `ConfigError: FACE_STABILIZER_MIN_VOTES no puede ser mayor que ...` | Config incoherente | Baja `MIN_VOTES` o sube `WINDOW` |
| `⚠️ face_recognition no está instalado...` | Falta dlib | Instala `requirements.txt` o usa el modo demo |
| `No se pudo enviar la notificación: ...` | El webhook falló | Revisa `ALERT_WEBHOOK_URL` (n8n/HA/Slack) |

---

### ¿Sigues atascado?

1. `python scripts/check_setup.py --read-frames`
2. `python server.py --check`
3. Revisa `data/security.db`:
   ```bash
   sqlite3 data/security.db "SELECT created_at, event_type, severity, description FROM events ORDER BY id DESC LIMIT 20;"
   ```
4. Abre un *issue* con: sistema operativo, versión de Python, salida de los dos comandos
   anteriores y las últimas líneas de log (**sin contraseñas**).
