# Contribuir a MCP Security Vision

Gracias por querer mejorar el proyecto. Esta guía resume el flujo para que tu
cambio se integre rápido y sin sorpresas.

## Antes de empezar

- Busca issues y pull requests existentes para no duplicar trabajo.
- Para cambios grandes, abre primero un issue y acordamos el enfoque.
- **No reportes vulnerabilidades en issues públicos**: sigue [SECURITY.md](SECURITY.md).

## Entorno de desarrollo

```bash
git clone https://github.com/kfr0st/mcp-security-vision.git
cd mcp-security-vision

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements-dev.txt
pip install --no-deps -e .
cp .env.example .env
python scripts/check_setup.py
```

`requirements-dev.txt` no instala torch ni dlib: los tests usan la cámara
sintética y modelos simulados (`VISION_FAKE_MODELS=1`).

## Comprobaciones obligatorias

Antes de abrir el pull request:

```bash
make lint      # ruff check + ruff format --check
make test      # pytest
```

O directamente:

```bash
ruff check .
ruff format --check .
pytest -q
```

Si tu cambio toca el arranque del servidor o las herramientas MCP, ejecuta
también el test de integración:

```bash
pytest -q -m integration
```

## Estilo del código

- Python 3.10–3.12, tipado moderno (`from __future__ import annotations`).
- Ruff con `line-length = 100`; usa `make format` antes de commitear.
- Mensajes de usuario y documentación en **español**; nombres de código en inglés.
- Mantén la arquitectura: la lógica vive en `mcp_security_vision/` y `server.py`
  solo traduce a herramientas MCP.
- Añade tests para cada corrección o funcionalidad nueva. Los tests no deben
  necesitar cámara, GPU, torch ni dlib.

## Datos personales en tests

No incluyas fotos reales de personas, direcciones IP privadas, credenciales ni
bases de datos de producción. Usa `known_faces/` vacío, imágenes sintéticas y
rutas temporales (`tmp_path`).

## Pull requests

1. Haz fork y crea una rama descriptiva: `feat/zonas-por-horario`,
   `fix/reconexion-rtsp`, `docs/instalacion-docker`.
2. Un PR = un cambio coherente. Evita mezclar refactors masivos con correcciones.
3. Rellena la plantilla del PR: qué problema resuelve, cómo lo probaste y qué
   documentación actualizaste.
4. Si cambias variables de entorno, herramientas MCP o el esquema SQLite,
   actualiza `README.md`, `.env.example` y `CHANGELOG.md`.
5. La CI debe quedar en verde (lint + tests en Python 3.10/3.11/3.12 × MCP 1.x/2.x).

## Commits

Usamos mensajes estilo [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: añade zonas por horario
fix: evita eventos duplicados al reconectar
docs: aclara el despliegue detrás de un proxy
test: cubre la validación de keep_days
```

## Licencia

Al contribuir aceptas que tu aportación se publique bajo la licencia MIT del
proyecto (ver [LICENSE](LICENSE)).
