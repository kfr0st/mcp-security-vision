#!/usr/bin/env python3
"""Punto de entrada del servidor MCP de videovigilancia.

Uso::

    python server.py                       # transporte stdio (Claude Desktop/Cursor)
    python server.py --transport streamable-http --host 127.0.0.1 --port 8000
    python server.py --check               # valida la configuración y sale

La implementación vive en ``mcp_security_vision/server.py`` para que también se
pueda instalar como paquete (``pip install .`` -> comando ``mcp-security-vision``).
"""

from __future__ import annotations

from mcp_security_vision.server import main

if __name__ == "__main__":
    raise SystemExit(main())
