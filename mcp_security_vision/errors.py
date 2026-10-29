"""Excepciones propias del paquete."""

from __future__ import annotations


class VisionError(Exception):
    """Error de negocio que se puede mostrar al agente tal cual."""


class ConfigError(VisionError, ValueError):
    """La configuración (variables de entorno) no es válida."""


class UnknownCameraError(VisionError):
    """El ``camera_id`` solicitado no existe o está deshabilitado."""


class CameraNotReadyError(VisionError):
    """La cámara todavía no ha entregado ningún fotograma."""


class FaceRegistrationError(VisionError):
    """No se pudo registrar el rostro (0 rostros, varios rostros, etc.)."""
