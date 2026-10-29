# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# MCP Security Vision · imagen lista para Docker Hub
#   docker build -t kfr0st/mcp-security-vision:latest .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v "$PWD/data:/app/data" ...
#
# La imagen compila dlib (para face-recognition) en una etapa "builder" y deja
# una imagen final slim sin compiladores.
# ---------------------------------------------------------------------------
ARG PYTHON_VERSION=3.11

# ----------------------------- ETAPA BUILDER -------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        libopenblas-dev \
        liblapack-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install --upgrade pip wheel setuptools

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY mcp_security_vision ./mcp_security_vision
RUN pip install --no-deps .

# Opcional: descargar yolov8n.pt durante el build (útil para despliegues sin salida a Internet)
ARG PRELOAD_MODELS=false
RUN mkdir -p /build/models \
    && if [ "${PRELOAD_MODELS}" = "true" ]; then \
         cd /build/models && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" ; \
       fi

# ----------------------------- ETAPA RUNTIME -------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:${PATH}" \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    DB_PATH=/app/data/security.db \
    KNOWN_FACES_DIR=/app/known_faces \
    SNAPSHOTS_DIR=/app/snapshots \
    ZONES_SEED_FILE=/app/config/zones.example.json \
    YOLO_MODEL=/app/models/yolov8n.pt \
    YOLO_CONFIG_DIR=/app/models/.ultralytics

# Librerías que necesita OpenCV en tiempo de ejecución
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libxcb1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system vision \
    && useradd --system --gid vision --create-home --uid 1000 vision

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/models /app/models

WORKDIR /app
COPY --chown=vision:vision server.py ./
COPY --chown=vision:vision mcp_security_vision ./mcp_security_vision
COPY --chown=vision:vision config ./config

RUN mkdir -p /app/data /app/snapshots /app/known_faces /app/models \
    && chown -R vision:vision /app

USER vision
EXPOSE 8000

VOLUME ["/app/data", "/app/snapshots", "/app/known_faces", "/app/models", "/app/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, socket; socket.create_connection(('127.0.0.1', int(os.getenv('MCP_PORT', '8000'))), 3).close()" || exit 1

CMD ["python", "server.py"]
