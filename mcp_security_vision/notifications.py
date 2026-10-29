"""Notificaciones opcionales por webhook (Telegram, n8n, Slack, Home Assistant...).

El pipeline de visión nunca espera a la red: los eventos se encolan y un hilo
dedicado hace los POST. Si el webhook falla, se registra y se sigue.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import Any

from .database import EventRecord

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


class WebhookNotifier:
    """Envía eventos a un webhook JSON en segundo plano."""

    def __init__(
        self,
        url: str,
        min_severity: str = "warning",
        timeout: float = 5.0,
        max_queue: int = 200,
    ) -> None:
        self.url = url
        self.min_severity = min_severity if min_severity in SEVERITY_ORDER else "warning"
        self.timeout = float(timeout)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="webhook-notifier", daemon=True)
        self.sent = 0
        self.failed = 0

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)

    def notify_events(self, events: list[EventRecord]) -> None:
        """Encola los eventos que superan el umbral de severidad."""
        for event in events:
            if SEVERITY_ORDER.get(event.severity, 0) < SEVERITY_ORDER[self.min_severity]:
                continue
            try:
                self._queue.put_nowait(event.to_dict())
            except queue.Full:
                logger.warning("Cola de notificaciones llena; se descarta el evento %s", event.id)

    # ------------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._post(payload)
                self.sent += 1
            except Exception as exc:  # pragma: no cover - depende de la red
                self.failed += 1
                logger.warning("No se pudo enviar la notificación: %s", exc)

    def _post(self, payload: dict[str, Any]) -> None:
        body = json.dumps(
            {"source": "mcp-security-vision", "event": payload}, ensure_ascii=False
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "mcp-security-vision/2"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read()
