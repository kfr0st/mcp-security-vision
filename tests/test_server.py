"""Tests de las herramientas MCP y de la seguridad del transporte HTTP."""

from __future__ import annotations

import asyncio

import pytest

from mcp_security_vision import server
from mcp_security_vision.server import _transport_security


def _request(host: str, origin: str | None = None):
    from starlette.requests import Request

    headers = [(b"host", host.encode("ascii"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }
    )


def test_transport_security_defaults_to_local_hosts(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

    security = _transport_security()
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert "localhost" in security.allowed_hosts
    assert "127.0.0.1:*" in security.allowed_hosts
    assert "[::1]" in security.allowed_hosts
    assert "http://localhost:*" in security.allowed_origins


def test_transport_security_env_extends_defaults(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com,mcp.example.com:*")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://mcp.example.com")

    security = _transport_security()
    assert security is not None
    assert "localhost" in security.allowed_hosts  # los defaults se conservan
    assert "mcp.example.com" in security.allowed_hosts
    assert "https://mcp.example.com" in security.allowed_origins


def test_transport_security_rejects_malicious_host_and_origin(monkeypatch):
    pytest.importorskip("mcp.server.transport_security")
    from mcp.server.transport_security import TransportSecurityMiddleware

    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    security = _transport_security()
    assert security is not None
    middleware = TransportSecurityMiddleware(security)

    bad_host = asyncio.run(middleware.validate_request(_request("evil.example.com")))
    assert bad_host is not None and bad_host.status_code == 421

    bad_origin = asyncio.run(
        middleware.validate_request(_request("localhost:8000", "https://evil.example.com"))
    )
    assert bad_origin is not None and bad_origin.status_code == 403

    ok = asyncio.run(
        middleware.validate_request(_request("localhost:8000", "http://localhost:3000"))
    )
    assert ok is None


class _FakeSystem:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.capture_kwargs: dict | None = None

    def cleanup(self, **kwargs):
        self.calls.append(kwargs)
        return "limpieza ejecutada"

    def capture_jpeg(self, **kwargs):
        self.capture_kwargs = kwargs
        return b"\xff\xd8\xff\xd9", None


def test_cleanup_history_requires_confirmation():
    fake = _FakeSystem()
    server.set_system(fake)
    try:
        message = server.cleanup_history(keep_days=10)
        assert "no ejecutada" in message
        assert fake.calls == []

        assert server.cleanup_history(keep_days=10, purge_snapshots=True, confirm=True) == (
            "limpieza ejecutada"
        )
        assert fake.calls == [{"keep_days": 10.0, "purge_snapshots": True}]
    finally:
        server.set_system(None)


def test_capture_snapshot_defaults_to_not_saving():
    fake = _FakeSystem()
    server.set_system(fake)
    try:
        image = server.capture_snapshot(annotate=True)
        assert fake.capture_kwargs == {"camera_id": "", "annotate": True, "save": False}
        assert image.data == b"\xff\xd8\xff\xd9"

        server.capture_snapshot(save=True)
        assert fake.capture_kwargs == {"camera_id": "", "annotate": False, "save": True}
    finally:
        server.set_system(None)
