"""Rutas y utilidades del sistema de archivos para BajaPe."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "BajaPe"
APP_ID = "BajaPe.Desktop.1"

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WINDOWS else 0
CREATE_NO_WINDOW_GROUP = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def app_data_dir() -> Path:
    """Carpeta de datos de la aplicación (configuración, historial y binarios)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / ".bajape")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def tools_dir() -> Path:
    path = app_data_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def history_path() -> Path:
    return app_data_dir() / "history.json"


def log_path() -> Path:
    return app_data_dir() / "bajape.log"


def default_download_dir() -> Path:
    """Devuelve la carpeta de descargas del usuario y crea BajaPe dentro."""
    home = Path.home()
    base = None
    for name in ("Downloads", "Descargas"):
        candidate = home / name
        if candidate.is_dir():
            base = candidate
            break
    if base is None:
        base = home
    path = base / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = home
    return path


def resource_path(relative: str) -> Path:
    """Ruta a un recurso, funciona en desarrollo y dentro del .exe de PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def open_path(path: str | Path) -> None:
    """Abre una carpeta o archivo con la aplicación predeterminada."""
    target = str(path)
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError:
        pass


def reveal_path(path: str | Path) -> None:
    """Abre el explorador con el archivo seleccionado."""
    target = Path(path)
    if not target.exists():
        open_path(target.parent)
        return
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", f"/select,{target}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])
    except OSError:
        open_path(target.parent)
