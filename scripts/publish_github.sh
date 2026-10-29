#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Publica MCP Security Vision en GitHub de forma segura.
#
# Uso:
#   bash scripts/publish_github.sh git@github.com:kfr0st/mcp-security-vision.git
#   bash scripts/publish_github.sh https://github.com/kfr0st/mcp-security-vision.git
#
# Crea el repositorio vacío antes en https://github.com/new (sin README ni licencia).
# ---------------------------------------------------------------------------
set -euo pipefail

REMOTE_URL="${1:-}"
BRANCH="main"
COMMIT_MSG="feat: MCP Security Vision v2.1.0 (YOLOv8 + rostros + zonas + SQLite + Docker)"

if [ -z "$REMOTE_URL" ]; then
  echo "Uso: bash scripts/publish_github.sh <URL_DEL_REPOSITORIO>"
  echo "Ejemplo: bash scripts/publish_github.sh git@github.com:kfr0st/mcp-security-vision.git"
  exit 1
fi

if [ ! -f "pyproject.toml" ] || [ ! -d "mcp_security_vision" ]; then
  echo "⛔ Ejecuta este script desde la raíz del proyecto (mcp-security-vision/)."
  exit 1
fi

if [ ! -f ".gitignore" ]; then
  echo "⛔ No existe .gitignore: me niego a subir sin él (subirías .env y fotos privadas)."
  exit 1
fi

echo "▶ 1/6 Inicializando repositorio git…"
if [ ! -d ".git" ]; then
  git init -b "$BRANCH"
else
  git branch --show-current >/dev/null 2>&1 || true
fi

echo "▶ 2/6 Añadiendo archivos…"
git add -A

echo "▶ 3/6 Revisando que no se suban secretos ni datos privados…"
BLOCKED=$(git diff --cached --name-only | grep -Ei \
  '(^|/)\.env$|\.db$|\.db-wal$|\.db-shm$|\.pt$|\.onnx$|^known_faces/.+\.(jpg|jpeg|png|webp)$|^snapshots/.+\.jpg$|^data/.+\.db' \
  || true)
if [ -n "$BLOCKED" ]; then
  echo "⛔ Abortado: estos archivos NO deben subirse:"
  echo "$BLOCKED" | sed 's/^/   - /'
  echo "   Quítalos del índice con: git rm --cached <archivo>"
  exit 1
fi
echo "   ✅ limpio"

echo "▶ 4/6 Creamos el commit inicial…"
if git rev-parse HEAD >/dev/null 2>&1; then
  echo "   Ya existe historial: se añade un commit nuevo."
else
  echo "   Primer commit."
fi
git commit -m "$COMMIT_MSG" || echo "   (nada nuevo que commitear)"

echo "▶ 5/6 Configurando remoto…"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi
git branch -M "$BRANCH"

echo "▶ 6/6 Subiendo a GitHub…"
git push -u origin "$BRANCH"

cat <<EOF

✅ Publicado.

Siguientes pasos recomendados:
  1. Secrets de Docker Hub (opcional, para publicar la imagen):
       Settings → Secrets and variables → Actions
       DOCKERHUB_USERNAME, DOCKERHUB_TOKEN
  2. Topics del repositorio:
       gh repo edit --add-topic mcp,model-context-protocol,yolov8,face-recognition,security,cctv,opencv,docker
  3. Primera release (dispara el build multi-arch):
       git tag -a v2.1.0 -m "v2.1.0" && git push origin v2.1.0
  4. Comprueba que la CI está en verde en la pestaña Actions.

Repositorio: ${REMOTE_URL%.git}
EOF
