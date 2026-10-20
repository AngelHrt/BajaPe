"""Funciones de formato y validacion compartidas."""

from __future__ import annotations

import math
import re
from datetime import datetime
from urllib.parse import urlparse

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def human_bytes(value: float | int | None) -> str:
    if not value or value <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    index = min(int(math.log(value, 1024)), len(units) - 1)
    scaled = value / (1024**index)
    if index == 0:
        return f"{int(scaled)} B"
    return f"{scaled:.1f} {units[index]}"


def human_speed(value: float | int | None) -> str:
    if not value or value <= 0:
        return ""
    return f"{human_bytes(value)}/s"


def human_eta(seconds: float | int | None) -> str:
    if seconds is None or seconds <= 0:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def truncate(text: str, limit: int = 80) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(str(value).strip())
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def normalize_url(value: str) -> str | None:
    """Normaliza una línea escrita por el usuario a una URL http(s) válida."""
    text = value.strip().strip('"').strip("'")
    if not text or text.startswith("#"):
        return None
    if not text.lower().startswith(("http://", "https://")):
        host = text.split("/", 1)[0].lower()
        if host not in _YOUTUBE_HOSTS:
            return None
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return text


def parse_urls(raw: str) -> list[str]:
    """Convierte un bloque de texto en una lista de URLs sin duplicados cercanos."""
    urls: list[str] = []
    seen: set[str] = set()
    for line in raw.replace(",", "\n").splitlines():
        normalized = normalize_url(line)
        if normalized and normalized not in seen:
            urls.append(normalized)
            seen.add(normalized)
    return urls


def is_playlist_url(url: str) -> bool:
    lowered = url.lower()
    return any(
        marker in lowered
        for marker in ("/playlist", "list=", "/@", "/channel/", "/c/", "/user/", "/videos")
    )


def friendly_error(raw: str) -> str:
    """Añade una pista en español a los errores más habituales de yt-dlp."""
    text = strip_ansi(raw).strip()
    clean = re.sub(r"^ERROR:\s*", "", text, flags=re.IGNORECASE)
    lowered = clean.lower()
    hint = ""
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        hint = "YouTube pidió verificación. Activa las cookies del navegador en Ajustes."
    elif "private video" in lowered:
        hint = "El video es privado."
    elif "age" in lowered and ("confirm" in lowered or "restricted" in lowered):
        hint = "Video con restricción de edad. Usa cookies del navegador en Ajustes."
    elif "video unavailable" in lowered or "this video is unavailable" in lowered:
        hint = "El video no está disponible."
    elif "http error 403" in lowered or "403 forbidden" in lowered:
        hint = "YouTube bloqueó la descarga. Actualiza yt-dlp o usa cookies del navegador."
    elif "unsupported url" in lowered:
        hint = "El enlace no es compatible."
    elif "no space left" in lowered or "not enough space" in lowered:
        hint = "No hay espacio suficiente en el disco."
    elif "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        hint = (
            "Error de certificado TLS. Revisa la fecha de Windows o la inspección "
            "HTTPS del antivirus/proxy. Si usas un proxy, guarda su certificado en "
            "%LOCALAPPDATA%\\BajaPe\\ca-bundle.pem."
        )
    if hint and hint.lower() not in clean.lower():
        return f"{hint} ({clean})"
    return clean


def format_datetime(iso_value: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_value)
        return dt.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return iso_value or ""
