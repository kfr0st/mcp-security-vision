"""Tests de los scripts de publicación (``configure_repo.py``)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "configure_repo", ROOT / "scripts" / "configure_repo.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["configure_repo"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_check_setup():
    spec = importlib.util.spec_from_file_location(
        "check_setup", ROOT / "scripts" / "check_setup.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_setup"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_configure_repo_rewrites_placeholders(tmp_path):
    script = _load_script()
    readme = tmp_path / "README.md"
    readme.write_text(
        "[![CI](https://github.com/tu-usuario/mcp-security-vision/actions/workflows/ci.yml/badge.svg)]"
        "(https://github.com/tu-usuario/mcp-security-vision/actions)\n"
        "docker pull tuusuario/mcp-security-vision:latest\n"
        "MIT © 2025 Tu Nombre. Ver LICENSE.\n",
        encoding="utf-8",
    )

    assert script.update_readme(readme, "maria-dev", "marialopez", "María López") is True
    text = readme.read_text(encoding="utf-8")
    assert "tu-usuario" not in text
    assert "tuusuario" not in text
    assert "github.com/maria-dev/mcp-security-vision" in text
    assert "docker pull marialopez/mcp-security-vision" in text
    assert "María López" in text


def test_configure_repo_is_idempotent(tmp_path):
    script = _load_script()
    readme = tmp_path / "README.md"
    readme.write_text("repo: tu-usuario/mcp-security-vision\n", encoding="utf-8")
    assert script.update_readme(readme, "ana", "ana", "Ana") is True
    assert script.update_readme(readme, "ana", "ana", "Ana") is False  # nada que cambiar


def test_configure_repo_pyproject_and_license(tmp_path):
    script = _load_script()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        'authors = [{ name = "Tu Nombre", email = "tu@email.com" }]\n'
        'Homepage = "https://github.com/tu-usuario/mcp-security-vision"\n'
        'Issues = "https://github.com/tu-usuario/mcp-security-vision/issues"\n',
        encoding="utf-8",
    )
    assert script.update_pyproject(pyproject, "luis", "Luis Paz", "luis@ejemplo.com") is True
    texto = pyproject.read_text(encoding="utf-8")
    assert 'name = "Luis Paz"' in texto
    assert 'email = "luis@ejemplo.com"' in texto
    assert "github.com/luis/mcp-security-vision" in texto

    licencia = tmp_path / "LICENSE"
    licencia.write_text("MIT License\n\nCopyright (c) 2020 Tu Nombre\n", encoding="utf-8")
    assert script.update_license(licencia, "Luis Paz") is True
    assert "Copyright (c) " in licencia.read_text(encoding="utf-8")
    assert "Luis Paz" in licencia.read_text(encoding="utf-8")


def test_configure_repo_updates_compose_and_makefile(tmp_path):
    script = _load_script()
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "image: ${DOCKERHUB_USER:-tu-usuario}/mcp-security-vision:latest\n"
        "extra: tuusuario/mcp-security-vision\n",
        encoding="utf-8",
    )
    makefile = tmp_path / "Makefile"
    makefile.write_text("IMAGE ?= tu-usuario/mcp-security-vision:latest\n", encoding="utf-8")

    assert script.update_compose(compose, "kfr0st") is True
    assert script.update_makefile(makefile, "kfr0st") is True
    assert "tu-usuario" not in compose.read_text(encoding="utf-8")
    assert "tuusuario" not in compose.read_text(encoding="utf-8")
    assert "kfr0st/mcp-security-vision" in makefile.read_text(encoding="utf-8")
    assert script.update_compose(compose, "kfr0st") is False  # idempotente


class _FakeSettings:
    def __init__(self, tmp_path: Path) -> None:
        self.transport = "stdio"
        self.db_path = tmp_path / "data" / "security.db"
        self.known_faces_dir = tmp_path / "known_faces"
        self.snapshots_dir = tmp_path / "snapshots"
        self.zones_seed_file = None
        self.cameras = []
        self.enabled_cameras = []
        self.fake_models = False
        self.faces_enabled = True

    def ensure_directories(self) -> None:
        for path in (self.db_path.parent, self.known_faces_dir, self.snapshots_dir):
            path.mkdir(parents=True, exist_ok=True)


def test_check_setup_returns_1_when_config_is_invalid(tmp_path, monkeypatch):
    check_setup = _load_check_setup()
    from mcp_security_vision.errors import ConfigError

    def fail(*_args, **_kwargs):
        raise ConfigError("configuración rota")

    monkeypatch.setattr(check_setup.Settings, "from_env", staticmethod(fail))
    assert check_setup.main([]) == 1


def test_check_setup_exit_status_reflects_missing_dependencies(tmp_path, monkeypatch, capsys):
    check_setup = _load_check_setup()
    fake = _FakeSettings(tmp_path)
    monkeypatch.setattr(check_setup.Settings, "from_env", staticmethod(lambda *a, **k: fake))
    monkeypatch.setattr(check_setup, "_module_available", lambda _name: False)

    assert check_setup.main([]) == 1
    output = capsys.readouterr().out
    assert "Falta la dependencia obligatoria" in output

    monkeypatch.setattr(check_setup, "_module_available", lambda _name: True)
    assert check_setup.main([]) == 0
    assert "Todo listo" in capsys.readouterr().out


def test_repo_has_no_private_files_tracked():
    """Barrido de seguridad: los archivos sensibles están ignorados por git."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "known_faces", "snapshots", "data", "*.pt", "*.db"):
        assert pattern in gitignore, f"falta {pattern!r} en .gitignore"

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".env", "known_faces", "snapshots", "data", "*.pt"):
        assert pattern in dockerignore, f"falta {pattern!r} en .dockerignore"
