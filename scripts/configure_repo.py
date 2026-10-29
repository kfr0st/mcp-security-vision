#!/usr/bin/env python3
"""Personaliza el repositorio antes de publicarlo (quita los placeholders).

Uso:
    python scripts/configure_repo.py --github-user miUsuario \
        --name "María López" --email maria@ejemplo.com \
        --dockerhub-user miusuario

Qué modifica:
    README.md          badges, URLs de clone/issues, referencias a Docker Hub
    pyproject.toml     autor, email, Homepage e Issues
    docker-compose.yml usuario de Docker Hub por defecto
    LICENSE            titular del copyright

El script es idempotente: puedes ejecutarlo varias veces.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_NAME = "mcp-security-vision"


def _replace_placeholders(text: str, github_user: str, dockerhub_user: str) -> str:
    """Sustituye tu-usuario/tuusuario por los valores reales (con o sin slug)."""
    text = text.replace("tu-usuario", github_user)
    text = text.replace("tuusuario", dockerhub_user)
    return text


def update_readme(path: Path, github_user: str, dockerhub_user: str, name: str) -> bool:
    original = path.read_text(encoding="utf-8")
    text = _replace_placeholders(original, github_user, dockerhub_user)
    text = re.sub(r"MIT © \d{4} [^.]+\.", f"MIT © {date.today().year} {name}.", text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def update_pyproject(path: Path, github_user: str, name: str, email: str | None) -> bool:
    original = path.read_text(encoding="utf-8")
    text = original
    text = text.replace(
        'authors = [{ name = "Tu Nombre", email = "tu@email.com" }]',
        f'authors = [{{ name = "{name}", email = "{email or ""}" }}]',
    )
    text = text.replace(
        'Homepage = "https://github.com/tu-usuario/mcp-security-vision"',
        f'Homepage = "https://github.com/{github_user}/{REPO_NAME}"',
    )
    text = text.replace(
        'Issues = "https://github.com/tu-usuario/mcp-security-vision/issues"',
        f'Issues = "https://github.com/{github_user}/{REPO_NAME}/issues"',
    )
    text = _replace_placeholders(text, github_user, github_user)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def update_compose(path: Path, dockerhub_user: str) -> bool:
    original = path.read_text(encoding="utf-8")
    text = _replace_placeholders(original, dockerhub_user, dockerhub_user)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def update_license(path: Path, name: str) -> bool:
    original = path.read_text(encoding="utf-8")
    text = re.sub(
        r"Copyright \(c\) \d{4} .+", f"Copyright (c) {date.today().year} {name}", original, count=1
    )
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def update_makefile(path: Path, dockerhub_user: str) -> bool:
    original = path.read_text(encoding="utf-8")
    text = _replace_placeholders(original, dockerhub_user, dockerhub_user)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def verify_no_placeholders() -> list[str]:
    """Devuelve los archivos que aún contienen placeholders."""
    pending: list[str] = []
    for relative in (
        "README.md",
        "pyproject.toml",
        "docker-compose.yml",
        "LICENSE",
        "Makefile",
    ):
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "tu-usuario" in text or "tuusuario" in text or "Tu Nombre" in text:
            pending.append(relative)
    return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Personaliza el repositorio antes de publicarlo")
    parser.add_argument("--github-user", default=None, help="Usuario u organización de GitHub")
    parser.add_argument(
        "--check", action="store_true", help="Solo comprueba si quedan placeholders"
    )
    parser.add_argument(
        "--dockerhub-user", default=None, help="Usuario de Docker Hub (por defecto, el de GitHub)"
    )
    parser.add_argument(
        "--name", default=None, help="Nombre del autor para LICENSE/pyproject/README"
    )
    parser.add_argument("--email", default=None, help="Email del autor (opcional)")
    args = parser.parse_args(argv)

    if args.check:
        pending = verify_no_placeholders()
        if pending:
            print("⚠️  Quedan placeholders en: " + ", ".join(pending))
            return 1
        print("✅ No quedan placeholders")
        return 0

    if not args.github_user:
        parser.error("--github-user es obligatorio (o usa --check)")

    dockerhub_user = args.dockerhub_user or args.github_user
    name = (args.name or "").strip()
    if not name:
        # Conserva el titular actual de LICENSE si no se especifica nombre
        licencia = (ROOT / "LICENSE").read_text(encoding="utf-8")
        match = re.search(r"Copyright \(c\) \d{4} (.+)", licencia)
        name = match.group(1).strip() if match else "Tu Nombre"
    changes: list[str] = []

    if update_readme(ROOT / "README.md", args.github_user, dockerhub_user, args.name):
        changes.append("README.md")
    if update_pyproject(ROOT / "pyproject.toml", args.github_user, name, args.email):
        changes.append("pyproject.toml")
    if update_compose(ROOT / "docker-compose.yml", dockerhub_user):
        changes.append("docker-compose.yml")
    if update_license(ROOT / "LICENSE", name):
        changes.append("LICENSE")
    if update_makefile(ROOT / "Makefile", dockerhub_user):
        changes.append("Makefile")

    if changes:
        print("✅ Archivos actualizados: " + ", ".join(changes))
    else:
        print("ℹ️  Nada que cambiar (¿ya estaba personalizado?)")

    pending = verify_no_placeholders()
    if pending:
        print("⚠️  Revisa a mano (placeholders restantes): " + ", ".join(pending))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
