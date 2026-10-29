"""Test de integración real: arranca ``server.py`` y habla MCP con él.

Usa ``VISION_FAKE_MODELS=1`` + cámara sintética, así que no necesita GPU,
torch, dlib ni hardware. Funciona con MCP SDK 1.x y 2.x.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = {
    "list_cameras",
    "get_live_status",
    "capture_snapshot",
    "get_security_logs",
    "get_security_report",
    "get_active_intrusions",
    "list_known_people",
    "register_current_face",
    "forget_known_person",
    "list_restricted_zones",
    "add_restricted_zone",
    "remove_restricted_zone",
    "cleanup_history",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read().decode() if process.stderr else ""
            raise AssertionError(f"El servidor terminó antes de tiempo. stderr:\n{stderr}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"El servidor no abrió el puerto {port} en {timeout}s")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Servidor MCP en subproceso con transporte streamable-http."""
    pytest.importorskip("mcp", reason="Requiere el SDK de MCP instalado")
    tmp_path = tmp_path_factory.mktemp("integration")
    port = _free_port()
    env = {
        **os.environ,
        "VISION_FAKE_MODELS": "1",
        "CAMERA_SOURCE": "synthetic",
        "CAMERA_ID": "cam1",
        "PROCESS_EVERY_N_FRAMES": "1",
        "EVENT_COOLDOWN_SECONDS": "0",
        "INTRUSION_COOLDOWN_SECONDS": "0",
        "MCP_TRANSPORT": "streamable-http",
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        "DB_PATH": str(tmp_path / "data" / "security.db"),
        "KNOWN_FACES_DIR": str(tmp_path / "known_faces"),
        "SNAPSHOTS_DIR": str(tmp_path / "snapshots"),
        "ZONES_SEED_FILE": "none",
        "LOG_LEVEL": "WARNING",
    }
    process = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, process)
        yield {"port": port, "process": process, "tmp_path": tmp_path}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


def _transport(url: str):
    """Devuelve el cliente streamable-http de la versión del SDK instalada."""
    try:  # SDK 1.x
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(url)
    except ImportError:  # SDK 2.x
        from mcp.client.streamable_http import streamable_http_client

        return streamable_http_client(url)


async def _exercise(url: str) -> dict:
    from mcp import ClientSession

    async with _transport(url) as streams:
        read_stream, write_stream = streams[0], streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}

            cameras = await session.call_tool("list_cameras", {})

            # Dar tiempo a que el pipeline analice algún fotograma
            status = None
            for _ in range(40):
                status = await session.call_tool("get_live_status", {})
                text = status.content[0].text
                if "CONECTADA" in text and "Objetos en escena" in text:
                    break
                await asyncio.sleep(0.25)

            zones = await session.call_tool(
                "add_restricted_zone",
                {
                    "camera_id": "cam1",
                    "name": "Entrada",
                    "points": [[0.3, 0.2], [0.7, 0.2], [0.7, 0.95], [0.3, 0.95]],
                },
            )
            zones_list = await session.call_tool("list_restricted_zones", {})

            # Esperar la intrusión registrada por el pipeline
            logs_text = ""
            for _ in range(40):
                logs = await session.call_tool("get_security_logs", {"limit": 20})
                logs_text = logs.content[0].text
                if "intrusion" in logs_text:
                    break
                await asyncio.sleep(0.25)

            snapshot = await session.call_tool("capture_snapshot", {"annotate": True})
            image_parts = [
                part for part in snapshot.content if getattr(part, "type", "") == "image"
            ]

            active = await session.call_tool("get_active_intrusions", {})
            people = await session.call_tool("list_known_people", {})
            report = await session.call_tool("get_security_report", {"hours": 1})

            return {
                "names": names,
                "cameras": cameras.content[0].text,
                "status": status.content[0].text,
                "zones": zones.content[0].text,
                "zones_list": zones_list.content[0].text,
                "logs": logs_text,
                "image_parts": len(image_parts),
                "image_data_len": len(image_parts[0].data) if image_parts else 0,
                "active": active.content[0].text,
                "people": people.content[0].text,
                "report": report.content[0].text,
            }


def test_server_exposes_expected_tools(server):
    result = asyncio.run(asyncio.wait_for(_exercise(f"http://127.0.0.1:{server['port']}/mcp"), 90))

    assert EXPECTED_TOOLS.issubset(result["names"])
    assert "cam1" in result["cameras"]
    assert "CONECTADA" in result["status"]
    assert "Entrada" in result["zones"]
    assert "Entrada" in result["zones_list"]
    assert "intrusion" in result["logs"]
    assert result["image_parts"] == 1
    assert result["image_data_len"] > 1000
    assert "Entrada" in result["active"] or "intrusos" in result["active"]
    assert "Personas conocidas" in result["people"] or "No hay personas" in result["people"]
    assert "Eventos totales" in result["report"]


def test_status_resource(server):
    """El recurso security://status devuelve JSON válido."""

    async def _read() -> str:
        from mcp import ClientSession

        transport = _transport(f"http://127.0.0.1:{server['port']}/mcp")
        async with transport as streams, ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            resources = await session.list_resources()
            uris = {str(resource.uri) for resource in resources.resources}
            assert "security://status" in uris
            content = await session.read_resource("security://status")
            return content.contents[0].text

    text = asyncio.run(asyncio.wait_for(_read(), 60))
    payload = json.loads(text)
    assert payload["cameras"][0]["camera_id"] == "cam1"
