"""Paleta y estilos visuales de BajaPe."""

from __future__ import annotations

BG = "#0E1014"
SURFACE = "#161A21"
SURFACE_ALT = "#1C212A"
SURFACE_HOVER = "#252C38"
BORDER = "#2A3140"
TEXT = "#F3F5F9"
TEXT_MUTED = "#98A2B3"
TEXT_FAINT = "#687385"
ACCENT = "#FF3B30"
ACCENT_HOVER = "#D92D24"
ACCENT_SOFT = "#3A1719"
SUCCESS = "#31C48D"
WARNING = "#F5A524"
ERROR = "#F04438"
INFO = "#3B82F6"

FONT = "Segoe UI"
FONT_MONO = "Consolas"

STATUS_META: dict[str, tuple[str, str, str]] = {
    # estado: (etiqueta, color de fondo, color de texto)
    "queued": ("En cola", SURFACE_HOVER, TEXT_MUTED),
    "preparing": ("Preparando", INFO, "#FFFFFF"),
    "downloading": ("Descargando", ACCENT, "#FFFFFF"),
    "processing": ("Procesando", WARNING, "#241600"),
    "done": ("Listo", SUCCESS, "#052E20"),
    "partial": ("Con errores", WARNING, "#241600"),
    "error": ("Error", ERROR, "#FFFFFF"),
    "cancelling": ("Cancelando", SURFACE_HOVER, TEXT_MUTED),
    "cancelled": ("Cancelado", SURFACE_HOVER, TEXT_MUTED),
}

PROGRESS_COLORS: dict[str, str] = {
    "queued": BORDER,
    "preparing": INFO,
    "downloading": ACCENT,
    "processing": WARNING,
    "done": SUCCESS,
    "partial": WARNING,
    "error": ERROR,
    "cancelling": TEXT_FAINT,
    "cancelled": TEXT_FAINT,
}
