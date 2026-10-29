# 🚀 Publicar MCP Security Vision en GitHub

Tres caminos, del más manual al más automático. Todos dejan el mismo resultado:
un repositorio limpio, con CI verde y listo para recibir estrellas.

> **Importante:** este repo **no debe** subir nunca `.env`, `data/*.db`,
> `known_faces/*.jpg`, `snapshots/*.jpg` ni `*.pt`. El `.gitignore` ya los excluye;
> al final hay comandos para verificarlo antes del primer push.

---

## 0. Preparación (una sola vez, en tu máquina)

```bash
cd mcp-security-vision

# 1) Comprueba que no hay secretos ni datos privados en lo que se va a subir
git init -b main
git add -A
git status --short
git ls-files | grep -E '(^|/)\.env$|\.db(-wal|-shm)?$|\.pt$|known_faces/.+\.(jpg|jpeg|png)$|snapshots/.+\.jpg$' \
  && echo "⛔ Hay archivos privados en el índice" || echo "✅ sin secretos ni datos privados"

# 2) Personaliza los placeholders (badges, clone URL, autor, Docker Hub).
#    Ya está aplicado para la cuenta kfr0st; solo hace falta si cambias de usuario.
python scripts/configure_repo.py --github-user kfr0st --name "kfr0st" --dockerhub-user kfr0st
#    (opcional: --email kfr0st@users.noreply.github.com --description "…")
```

Qué cambia `configure_repo.py`:

| Archivo | Qué reemplaza |
|---|---|
| `README.md` | badges, URLs de clone/issues, referencias a Docker Hub |
| `pyproject.toml` | autor, email, `Homepage`/`Issues`, comando de instalación |
| `docker-compose.yml` | usuario de Docker Hub por defecto |
| `LICENSE` | titular del copyright |

---

## 1. Publicar con el script incluido (recomendado)

```bash
bash scripts/publish_github.sh git@github.com:kfr0st/mcp-security-vision.git
# o por HTTPS:
bash scripts/publish_github.sh https://github.com/kfr0st/mcp-security-vision.git
```

El script:

1. Verifica que estás en la raíz del proyecto y que existe `.gitignore`.
2. **Aborta si detecta** `.env`, `*.db`, `*.pt` o fotos de `known_faces/` en el índice.
3. Hace `git add`, commit inicial (si no hay ninguno) y `git push -u origin main`.
4. Imprime los siguientes pasos (topics, secrets, release).

> Crea antes el repositorio **vacío** en <https://github.com/new> (sin README, sin
> licencia, sin `.gitignore`: ya los tienes) para que el push no choque.

---

## 2. Publicar a mano (git clásico)

```bash
# Crea el repo vacío en la web de GitHub y luego:
cd mcp-security-vision
git init -b main
git add -A
git commit -m "feat: MCP Security Vision v2.1.0 (YOLOv8 + rostros + zonas + SQLite + Docker)"
git remote add origin https://github.com/kfr0st/mcp-security-vision.git
git push -u origin main
```

Si tu cuenta usa SSH:

```bash
git remote set-url origin git@github.com:kfr0st/mcp-security-vision.git
```

---

## 3. Publicar con GitHub CLI (`gh`)

```bash
gh auth login                      # una vez

cd mcp-security-vision
git init -b main && git add -A
git commit -m "feat: MCP Security Vision v2.1.0"
gh repo create mcp-security-vision --public --source=. --remote=origin --push \
  --description "Servidor MCP de videovigilancia inteligente: YOLOv8 + reconocimiento facial + zonas de intrusión + SQLite"
```

Añade los **topics** (mejoran el descubrimiento):

```bash
gh repo edit --add-topic mcp,model-context-protocol,yolov8,face-recognition,security,cctv,opencv,fastapi,docker
```

---

## 4. Dejar al agente de opencode / a otro agente

Pega este prompt tal cual en tu agente (opencode, Claude Code, Cursor…). Está escrito
para que **no** publique secretos y para que pregunte lo mínimo:

```text
Tarea: publicar el proyecto local /ruta/a/mcp-security-vision en un repositorio
nuevo de GitHub llamado mcp-security-vision, rama main.

Reglas obligatorias:
1. NO subas nunca: .env, archivos *.db / *.db-wal / *.db-shm, modelos *.pt,
   fotos de known_faces/, capturas de snapshots/, ni nada dentro de data/.
   Verifica con: git ls-files | grep -E '(^|/)\.env$|\.db$|\.pt$' y detente si hay resultados.
2. Antes de commitear, comprueba que .gitignore existe y que `git status --short`
   no muestra esos archivos.
3. Si falta personalizar, ejecuta:
   python scripts/configure_repo.py --github-user <MI_USUARIO>
   (pregúntame el usuario de GitHub y de Docker Hub si no los tienes).
4. Inicializa git en la rama main, haz un único commit inicial con el mensaje
   "feat: MCP Security Vision v2.1.0 (YOLOv8 + rostros + zonas + SQLite + Docker)".
5. Crea el repositorio remoto y sube con una de estas vías, según lo que esté disponible:
   - gh repo create mcp-security-vision --public --source=. --remote=origin --push
   - o crea el repo vacío en la web y luego:
     git remote add origin <URL> && git push -u origin main
6. NO me pidas ni pegues tokens en el chat. Usa `gh auth login` o el credential
   helper ya configurado en la máquina.
7. Al terminar: añade los topics mcp, model-context-protocol, yolov8,
   face-recognition, security, cctv, opencv, docker; crea el tag v2.1.0
   (git tag -a v2.1.0 -m "v2.1.0" && git push --tags) e imprime la URL del repo.
8. Si algo falla (permisos, remoto existente, CI rojo), muéstrame el error exacto
   y propón la corrección sin reescribir el historial (nada de force-push).
```

> Si el agente no tiene `gh` instalado: `brew install gh` / `sudo apt install gh` /
> `winget install GitHub.cli`, y luego `gh auth login`.

---

## 5. Después del primer push (checklist de 10 minutos)

### 5.1 Verifica que la CI pasa

- Pestaña **Actions** → workflow **CI**.
- Corre lint + 144 tests en Python 3.10/3.11/3.12 × MCP SDK 1.x/2.x + smoke test de Docker.
- Si falla algo, el log indica el archivo y la línea; los tests también corren en local:
  ```bash
  pip install -r requirements-dev.txt && pip install --no-deps -e .
  make lint && make test
  ```

### 5.2 Añade los secrets de Docker Hub (solo si publicarás la imagen)

Repositorio → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Valor |
|---|---|
| `DOCKERHUB_USERNAME` | tu usuario de Docker Hub |
| `DOCKERHUB_TOKEN` | token de *Docker Hub → Account settings → Security → New Access Token* (permiso Read & Write) |
| `DOCKERHUB_REPO` *(opcional)* | nombre del repo si no es `mcp-security-vision` |

Sin estos secrets la CI **no falla**: el workflow de Docker solo construye, no publica.

### 5.3 Crea una release

```bash
git tag -a v2.1.0 -m "v2.1.0 — revisión de seguridad y robustez"
git push origin v2.1.0
```

Esto dispara `docker-publish.yml` con tags `2.1.0`, `2.1`, `2`, `latest`
(multi-arquitectura `linux/amd64` + `linux/arm64`).

### 5.4 Detalles del repositorio

- **About**: descripción corta + topics (los del comando `gh repo edit`).
- **Settings → General**: desactiva *Allow merge commits* si prefieres squash.
- **Settings → Branches**: protege `main` exigiendo que pase el check **CI**.
- **Settings → Security**: activa *Dependabot alerts* y *Secret scanning*.
- **Insights → Community Standards**: el repo ya incluye `CONTRIBUTING.md`, `SECURITY.md`,
  plantillas de issue y plantilla de pull request.

### 5.5 Comprobación final del contenido publicado

```bash
# ¿Se subió algún secreto o archivo privado?
git ls-files | grep -Ei '\.env$|\.db$|\.pt$|known_faces/.+\.(jpg|png)$' || echo "✅ limpio"

# ¿El README se ve bien?
# Abre https://github.com/kfr0st/mcp-security-vision
```

Si subiste un secreto por error: **rota la contraseña** primero, luego
`git rm --cached .env`, commit y push. Si el secreto llegó al historial, reescribe
con `git filter-repo --path .env --invert-paths` y fuerza el push (coordínalo antes).

---

## 6. Problemas típicos al publicar

| Error | Causa | Solución |
|---|---|---|
| `remote: Repository not found` | URL mal escrita o sin permisos | Revisa usuario/repo y `gh auth status` |
| `Updates were rejected because the remote contains work…` | Creaste el repo con README/licencia | `git pull --rebase origin main` o crea el repo **vacío** |
| `refusing to allow a Personal Access Token to create…` | Token sin scope | Regenera el token con `repo` + `workflow` |
| `GH013: Repository rule violations` | Push protection detectó un secreto | Elimínalo del commit; rota la credencial |
| CI roja en `smoke-docker` | Docker Hub lento en el primer build | Re-ejecuta el job; el build tarda ~6-10 min por dlib |
| `Permission denied (publickey)` | SSH sin clave cargada | `ssh-keygen -t ed25519` + añade la pública en GitHub |
| `fatal: not a git repository` | Ejecutaste el script en otra carpeta | `cd mcp-security-vision` |

---

## 7. ¿Y si quieres que lo suba un agente?

El agente que trabajó en este proyecto **no tiene credenciales de GitHub** (ni debe
tenerlas), así que el push lo tienes que autorizar tú. Lo que sí está hecho:

- repositorio completo, con tests y documentación, listo en tu máquina;
- `scripts/publish_github.sh` para que el push sea un comando;
- `scripts/configure_repo.py` para quitar placeholders si cambias de cuenta;
- este documento con el prompt listo para opencode.

---

## Apéndice · comunidad

El repositorio ya incluye `CONTRIBUTING.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/`
y `.github/pull_request_template.md`. GitHub los detectará automáticamente en
**Insights → Community Standards**.
