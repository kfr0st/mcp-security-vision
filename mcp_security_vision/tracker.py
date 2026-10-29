"""Tracker sencillo por IoU.

No pretende ser ByteTrack/SORT: solo asigna IDs estables a los objetos entre
fotogramas consecutivos para poder decir "la misma persona entró en la zona y
sigue dentro", en lugar de disparar un evento nuevo por cada frame.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .detectors import Box, Detection


def iou(a: Box, b: Box) -> float:
    """Intersection over Union de dos cajas (x1, y1, x2, y2)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union > 0 else 0.0


@dataclass
class Track:
    """Pista activa (un objeto seguido en el tiempo)."""

    track_id: int
    label: str
    box: Box
    first_seen: float
    last_seen: float
    hits: int = 1


@dataclass(frozen=True)
class TrackedDetection:
    """Detección con el ID de pista asignado."""

    track_id: int
    detection: Detection
    is_new: bool


@dataclass
class SimpleTracker:
    """Emparejamiento voraz por IoU con expiración por tiempo."""

    iou_threshold: float = 0.3
    max_age_seconds: float = 2.0
    _tracks: dict[int, Track] = field(default_factory=dict)
    _next_id: int = 1

    def update(
        self, detections: list[Detection], now: float | None = None
    ) -> list[TrackedDetection]:
        now = time.monotonic() if now is None else now

        # 1. Expirar pistas viejas
        expired = [
            track_id
            for track_id, track in self._tracks.items()
            if now - track.last_seen > self.max_age_seconds
        ]
        for track_id in expired:
            del self._tracks[track_id]

        # 2. Candidatos (misma etiqueta) ordenados por IoU descendente
        candidates: list[tuple[float, int, int]] = []
        for det_index, detection in enumerate(detections):
            for track_id, track in self._tracks.items():
                if track.label != detection.label:
                    continue
                score = iou(track.box, detection.box)
                if score >= self.iou_threshold:
                    candidates.append((score, track_id, det_index))
        candidates.sort(key=lambda item: item[0], reverse=True)

        assigned_tracks: set[int] = set()
        assigned_detections: set[int] = set()
        results: dict[int, TrackedDetection] = {}
        for _score, track_id, det_index in candidates:
            if track_id in assigned_tracks or det_index in assigned_detections:
                continue
            assigned_tracks.add(track_id)
            assigned_detections.add(det_index)
            track = self._tracks[track_id]
            track.box = detections[det_index].box
            track.last_seen = now
            track.hits += 1
            results[det_index] = TrackedDetection(
                track_id=track_id, detection=detections[det_index], is_new=False
            )

        # 3. Detecciones sin pista -> pistas nuevas
        for det_index, detection in enumerate(detections):
            if det_index in assigned_detections:
                continue
            track_id = self._next_id
            self._next_id += 1
            self._tracks[track_id] = Track(
                track_id=track_id,
                label=detection.label,
                box=detection.box,
                first_seen=now,
                last_seen=now,
            )
            results[det_index] = TrackedDetection(
                track_id=track_id, detection=detection, is_new=True
            )

        return [results[index] for index in range(len(detections))]

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks.values())
