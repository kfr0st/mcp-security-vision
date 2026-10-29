"""Tests del notificador por webhook con un servidor HTTP local."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from mcp_security_vision.database import EventRecord
from mcp_security_vision.notifications import WebhookNotifier


class _CaptureHandler(BaseHTTPRequestHandler):
    received: ClassVar[list[dict]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).received.append(json.loads(body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):  # silenciar logs del servidor de prueba
        return


def _event(severity: str, description: str = "Intrusión en zona Patio") -> EventRecord:
    return EventRecord(
        id=1,
        camera_id="cam1",
        created_at="2026-01-01T10:00:00",
        event_type="intrusion",
        severity=severity,
        description=description,
    )


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/alertas"


def test_webhook_sends_events():
    _CaptureHandler.received = []
    server, url = _start_server()
    notifier = WebhookNotifier(url, min_severity="warning")
    notifier.start()
    try:
        notifier.notify_events([_event("warning"), _event("critical", "Cámara sin señal")])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(_CaptureHandler.received) < 2:
            time.sleep(0.05)
        assert len(_CaptureHandler.received) == 2
        assert _CaptureHandler.received[0]["source"] == "mcp-security-vision"
        assert _CaptureHandler.received[0]["event"]["camera_id"] == "cam1"
        assert notifier.sent == 2
    finally:
        notifier.stop()
        server.shutdown()


def test_webhook_filters_by_severity():
    _CaptureHandler.received = []
    server, url = _start_server()
    notifier = WebhookNotifier(url, min_severity="critical")
    notifier.start()
    try:
        notifier.notify_events([_event("info"), _event("warning")])
        time.sleep(0.3)
        assert _CaptureHandler.received == []

        notifier.notify_events([_event("critical")])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _CaptureHandler.received:
            time.sleep(0.05)
        assert len(_CaptureHandler.received) == 1
    finally:
        notifier.stop()
        server.shutdown()
