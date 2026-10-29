.PHONY: help install install-dev run run-http check demo test lint format docker-build docker-run docker-push clean

PYTHON ?= python3
IMAGE  ?= kfr0st/mcp-security-vision:latest

help:  ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Instala dependencias completas (YOLO + dlib)
	$(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

install-dev:  ## Instala dependencias ligeras de desarrollo/CI
	$(PYTHON) -m pip install -r requirements-dev.txt

run:  ## Arranca el servidor en stdio (Claude Desktop / Cursor)
	$(PYTHON) server.py

run-http:  ## Arranca el servidor HTTP en 127.0.0.1:8000 (solo local)
	$(PYTHON) server.py --transport streamable-http --host 127.0.0.1 --port 8000

check:  ## Valida la configuración (.env, rutas)
	$(PYTHON) server.py --check

demo:  ## Demo sin cámara ni modelos reales (cámara sintética + detectores falsos)
	VISION_FAKE_MODELS=1 CAMERA_SOURCE=synthetic MCP_TRANSPORT=streamable-http \
	  MCP_HOST=127.0.0.1 MCP_PORT=8000 $(PYTHON) server.py

test:  ## Ejecuta los tests con cobertura
	$(PYTHON) -m pytest --cov --cov-report=term-missing

lint:  ## Comprueba estilo y errores con ruff
	ruff check .

format:  ## Formatea el código con ruff
	ruff format .
	ruff check --fix .

docker-build:  ## Construye la imagen local
	docker build -t $(IMAGE) .

docker-run:  ## Ejecuta el contenedor con el .env local
	docker run --rm -p 8000:8000 --env-file .env \
	  -v "$(PWD)/data:/app/data" -v "$(PWD)/snapshots:/app/snapshots" \
	  -v "$(PWD)/known_faces:/app/known_faces" $(IMAGE)

docker-push:  ## Publica la imagen en Docker Hub (requiere docker login)
	docker push $(IMAGE)

clean:  ## Limpia cachés
	rm -rf .pytest_cache .ruff_cache __pycache__ */__pycache__ .coverage htmlcov
