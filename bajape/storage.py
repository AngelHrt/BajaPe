"""Persistencia local: ajustes e historial en JSON."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

logger = logging.getLogger("bajape.storage")

DEFAULT_SETTINGS: dict[str, Any] = {
    "download_dir": "",
    "kind": "video",
    "video_quality": "1080",
    "audio_bitrate": "192",
    "include_playlists": True,
    "playlist_limit": 0,
    "max_concurrent": 3,
    "cookies_browser": "",
    "open_folder_when_done": False,
    "window_geometry": "",
}

VALID_KINDS = {"video", "audio"}
VALID_VIDEO_QUALITIES = {"best", "2160", "1440", "1080", "720", "480", "360"}
VALID_AUDIO_BITRATES = {"320", "256", "192", "128"}
VALID_COOKIE_BROWSERS = {"", "chrome", "edge", "firefox", "brave"}


def _atomic_write(path: Path, payload: str) -> None:
    tmp = path.parent / (path.name + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.exception("No se pudo guardar %s", path)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


class SettingsStore:
    """Ajustes de la aplicación con validación y guardado atómico."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.settings_path()
        self._lock = threading.RLock()
        self.data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        raw: dict[str, Any] = {}
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
        except (OSError, ValueError):
            logger.exception("No se pudo leer la configuración; se usan valores por defecto")

        data = dict(DEFAULT_SETTINGS)
        data.update({key: value for key, value in raw.items() if key in DEFAULT_SETTINGS})
        self.data = self._validate(data)

    @staticmethod
    def _validate(data: dict[str, Any]) -> dict[str, Any]:
        valid = dict(DEFAULT_SETTINGS)
        valid.update(data)

        download_dir = str(valid.get("download_dir") or "").strip()
        if not download_dir or not Path(download_dir).is_dir():
            valid["download_dir"] = str(paths.default_download_dir())
        else:
            valid["download_dir"] = download_dir

        if valid.get("kind") not in VALID_KINDS:
            valid["kind"] = "video"
        if valid.get("video_quality") not in VALID_VIDEO_QUALITIES:
            valid["video_quality"] = "1080"
        if valid.get("audio_bitrate") not in VALID_AUDIO_BITRATES:
            valid["audio_bitrate"] = "192"
        if valid.get("cookies_browser") not in VALID_COOKIE_BROWSERS:
            valid["cookies_browser"] = ""

        valid["include_playlists"] = bool(valid.get("include_playlists", True))
        valid["open_folder_when_done"] = bool(valid.get("open_folder_when_done", False))
        valid["playlist_limit"] = max(0, min(10000, int(valid.get("playlist_limit") or 0)))
        valid["max_concurrent"] = max(1, min(4, int(valid.get("max_concurrent") or 3)))
        valid["window_geometry"] = str(valid.get("window_geometry") or "")
        return valid

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.data.get(key, default)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            self.data[key] = value
        if save:
            self.save()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self.data.update(values)
        self.save()

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        _atomic_write(self.path, payload)


class HistoryStore:
    """Historial de archivos descargados, del más reciente al más antiguo."""

    MAX_ENTRIES = 2000

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.history_path()
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        entries: list[dict[str, Any]] = []
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    for item in loaded:
                        if not isinstance(item, dict):
                            continue
                        title = str(item.get("title") or "").strip()
                        filepath = str(item.get("path") or "").strip()
                        if not title and not filepath:
                            continue
                        entries.append(
                            {
                                "title": title or Path(filepath).stem,
                                "url": str(item.get("url") or ""),
                                "path": filepath,
                                "kind": "audio" if item.get("kind") == "audio" else "video",
                                "quality": str(item.get("quality") or ""),
                                "timestamp": str(item.get("timestamp") or ""),
                            }
                        )
        except (OSError, ValueError):
            logger.exception("No se pudo leer el historial")
        self._entries = entries[: self.MAX_ENTRIES]

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def add(
        self,
        title: str,
        url: str,
        path: str | Path,
        kind: str,
        quality: str,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "title": title.strip() or Path(str(path)).stem,
            "url": url,
            "path": str(path),
            "kind": "audio" if kind == "audio" else "video",
            "quality": quality,
            "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            # Evita duplicar exactamente el mismo archivo descargado dos veces seguidas.
            if self._entries and self._entries[0].get("path") == entry["path"]:
                return dict(self._entries[0])
            self._entries.insert(0, entry)
            del self._entries[self.MAX_ENTRIES :]
        self.save()
        return dict(entry)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        self.save()

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._entries, ensure_ascii=False, indent=2)
        _atomic_write(self.path, payload)
