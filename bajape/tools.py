"""Gestión de binarios externos: yt-dlp, ffmpeg/ffprobe y runtime de JavaScript."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import net, paths

logger = logging.getLogger("bajape.tools")

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
)

ProgressCallback = Callable[[str, float, str], None]

_YTDLP_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}")
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg version\s+(\S+)", re.IGNORECASE)


class ToolError(RuntimeError):
    """Error al localizar, descargar o validar una herramienta."""


class ToolCancelled(RuntimeError):
    """El usuario canceló la preparación de herramientas."""


@dataclass
class ToolStatus:
    ytdlp: Path | None = None
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    js_runtime: str | None = None
    js_path: Path | None = None

    @property
    def ready(self) -> bool:
        return self.ytdlp is not None and self.ffmpeg is not None

    @property
    def missing(self) -> list[str]:
        names: list[str] = []
        if self.ytdlp is None:
            names.append("yt-dlp")
        if self.ffmpeg is None:
            names.append("ffmpeg")
        return names

    @property
    def has_js_runtime(self) -> bool:
        return self.js_runtime is not None


class ToolManager:
    """Descarga y actualiza las herramientas que usa la aplicación."""

    def __init__(self) -> None:
        self.bin_dir = paths.tools_dir()
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._last_progress: dict[str, float] = {}
        # Se activa desde la ventana de preparación si el usuario acepta
        # descargar sin validar el certificado (redes con proxy o antivirus).
        self.allow_insecure = net.insecure_allowed()

    # ------------------------------------------------------------------ estado
    def locate(self) -> ToolStatus:
        status = ToolStatus()

        managed_ytdlp = self.bin_dir / "yt-dlp.exe"
        if managed_ytdlp.is_file():
            status.ytdlp = managed_ytdlp
        else:
            found = shutil.which("yt-dlp")
            if found:
                status.ytdlp = Path(found)

        managed_ffmpeg = self.bin_dir / "ffmpeg.exe"
        if managed_ffmpeg.is_file():
            status.ffmpeg = managed_ffmpeg
        else:
            found = shutil.which("ffmpeg")
            if found:
                status.ffmpeg = Path(found)

        if status.ffmpeg is not None:
            managed_probe = self.bin_dir / "ffprobe.exe"
            if managed_probe.is_file():
                status.ffprobe = managed_probe
            else:
                sibling = status.ffmpeg.with_name("ffprobe.exe")
                if sibling.is_file():
                    status.ffprobe = sibling
                else:
                    found = shutil.which("ffprobe")
                    if found:
                        status.ffprobe = Path(found)

        runtime = self._find_js_runtime()
        if runtime is not None:
            status.js_runtime, status.js_path = runtime
        return status

    def _find_js_runtime(self) -> tuple[str, Path] | None:
        managed_deno = self.bin_dir / "deno.exe"
        if managed_deno.is_file():
            return "deno", managed_deno
        for name, binaries in (
            ("deno", ("deno",)),
            ("node", ("node",)),
            ("bun", ("bun",)),
            ("quickjs", ("qjs", "quickjs")),
        ):
            for binary in binaries:
                found = shutil.which(binary)
                if found:
                    return name, Path(found)
        # Rutas habituales cuando el ejecutable no está en el PATH del proceso.
        for name, patterns in (
            ("deno", (r"%USERPROFILE%\.deno\bin\deno.exe", r"%LOCALAPPDATA%\deno\bin\deno.exe")),
            (
                "node",
                (
                    r"%ProgramFiles%\nodejs\node.exe",
                    r"%ProgramFiles(x86)%\nodejs\node.exe",
                    r"%LOCALAPPDATA%\Programs\nodejs\node.exe",
                    r"%USERPROFILE%\scoop\apps\nodejs\current\node.exe",
                    r"%APPDATA%\fnm\node-versions\*\installation\node.exe",
                    r"%LOCALAPPDATA%\fnm\node-versions\*\installation\node.exe",
                    r"%APPDATA%\nvm\v*\node.exe",
                ),
            ),
            ("bun", (r"%USERPROFILE%\.bun\bin\bun.exe", r"%LOCALAPPDATA%\bun\bun.exe")),
        ):
            for candidate in self._glob_candidates(patterns):
                return name, candidate
        return None

    @staticmethod
    def _glob_candidates(patterns: tuple[str, ...]):
        import glob as glob_module

        for pattern in patterns:
            expanded = os.path.expandvars(pattern)
            if "*" in expanded:
                for match in sorted(glob_module.glob(expanded), reverse=True):
                    path = Path(match)
                    if path.is_file():
                        yield path
            else:
                path = Path(expanded)
                if path.is_file():
                    yield path


    def js_args(self, status: ToolStatus | None = None) -> list[str]:
        status = status or self.locate()
        if not status.js_runtime:
            return []
        if status.js_path is not None:
            # La ruta explícita es necesaria cuando Node/Deno no está en el PATH
            # del proceso (por ejemplo, instalaciones de fnm o nvm).
            return ["--js-runtimes", f"{status.js_runtime}:{status.js_path}"]
        return ["--js-runtimes", status.js_runtime]

    def tls_args(self) -> list[str]:
        """Opciones de TLS para yt-dlp según la configuración del equipo."""
        args: list[str] = []
        bundle = net.ca_bundle_path()
        if bundle is not None:
            args += ["--ca-bundle", str(bundle)]
        if self.allow_insecure:
            args.append("--no-check-certificates")
        return args

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PATH"] = str(self.bin_dir) + os.pathsep + env.get("PATH", "")
        return env

    def ffmpeg_location(self, status: ToolStatus | None = None) -> str | None:
        status = status or self.locate()
        if status.ffmpeg is None:
            return None
        return str(status.ffmpeg.parent)

    # --------------------------------------------------------------- versiones
    @staticmethod
    def _run_capture(command: list[str], timeout: float = 10.0) -> str:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=paths.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return (completed.stdout or "").strip()

    def ytdlp_version(self, status: ToolStatus | None = None) -> str:
        status = status or self.locate()
        if status.ytdlp is None:
            return ""
        text = self._run_capture([str(status.ytdlp), "--version"])
        match = _YTDLP_VERSION_RE.search(text)
        if match:
            return match.group(0)
        return text.splitlines()[0].strip() if text else ""

    def ffmpeg_version(self, status: ToolStatus | None = None) -> str:
        status = status or self.locate()
        if status.ffmpeg is None:
            return ""
        text = self._run_capture([str(status.ffmpeg), "-version"])
        match = _FFMPEG_VERSION_RE.search(text)
        return match.group(1) if match else ""

    # ------------------------------------------------------------- preparación
    def cancel(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    def ensure(self, progress: ProgressCallback) -> ToolStatus:
        """Garantiza yt-dlp, ffmpeg y (si es posible) un runtime de JavaScript."""
        with self._lock:
            self.reset_cancel()
            status = self.locate()

            ytdlp_ok = status.ytdlp is not None and bool(self.ytdlp_version(status))
            if not ytdlp_ok:
                if status.ytdlp is not None and status.ytdlp.parent == self.bin_dir:
                    status.ytdlp.unlink(missing_ok=True)
                self._progress(progress, "ytdlp", 0.0, "Descargando yt-dlp...", force=True)
                self._download_ytdlp(progress)
            else:
                version = self.ytdlp_version(status)
                self._progress(progress, "ytdlp", 1.0, f"yt-dlp {version or 'listo'}", force=True)

            self._check_cancelled()
            status = self.locate()
            ffmpeg_ok = (
                status.ffmpeg is not None
                and status.ffprobe is not None
                and bool(self.ffmpeg_version(status))
            )
            if not ffmpeg_ok:
                self._progress(
                    progress, "ffmpeg", 0.0, "Descargando ffmpeg (unos 110 MB)...", force=True
                )
                self._download_ffmpeg(progress)
            else:
                version = self.ffmpeg_version(status)
                self._progress(
                    progress, "ffmpeg", 1.0, f"ffmpeg {version or 'listo'}", force=True
                )

            self._check_cancelled()
            status = self.locate()
            if status.js_runtime is None:
                self._progress(progress, "runtime", 0.0, "Descargando Deno...", force=True)
                try:
                    self._download_deno(progress)
                except ToolCancelled:
                    raise
                except Exception as error:  # noqa: BLE001 - se informa como advertencia
                    logger.warning("No se pudo instalar Deno: %s", error)
                    self._progress(
                        progress,
                        "runtime",
                        1.0,
                        "Sin runtime JS: algunas calidades pueden faltar",
                        force=True,
                    )
            else:
                self._progress(
                    progress,
                    "runtime",
                    1.0,
                    f"{status.js_runtime} detectado",
                    force=True,
                )

            return self.locate()

    def update_ytdlp(self, progress: ProgressCallback) -> str:
        """Fuerza la descarga de la última versión de yt-dlp."""
        with self._lock:
            self.reset_cancel()
            self._progress(progress, "ytdlp", 0.0, "Descargando última versión...", force=True)
            self._download_ytdlp(progress)
            version = self.ytdlp_version()
            self._progress(progress, "ytdlp", 1.0, f"yt-dlp {version} listo", force=True)
            return version

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise ToolCancelled("Preparación cancelada")

    # -------------------------------------------------------------- descargas
    def _download_ytdlp(self, progress: ProgressCallback) -> None:
        destination = self.bin_dir / "yt-dlp.exe"
        staged = self.bin_dir / "yt-dlp.new.exe"
        staged.unlink(missing_ok=True)
        self._download(
            YTDLP_URL,
            staged,
            progress,
            "ytdlp",
            "Descargando yt-dlp",
            "yt-dlp",
        )
        version = self.ytdlp_version(ToolStatus(ytdlp=staged))
        if not version:
            staged.unlink(missing_ok=True)
            raise ToolError("yt-dlp se descargó pero no se pudo ejecutar.")
        try:
            os.replace(staged, destination)
        except OSError as error:
            staged.unlink(missing_ok=True)
            raise ToolError(
                "No se pudo reemplazar yt-dlp. Cierra otras descargas e inténtalo de nuevo."
            ) from error
        self._progress(progress, "ytdlp", 1.0, f"yt-dlp {version} instalado", force=True)

    def _download_ffmpeg(self, progress: ProgressCallback) -> None:
        archive = self.bin_dir / "ffmpeg-download.zip"
        archive.unlink(missing_ok=True)
        last_error: Exception | None = None
        downloaded = False
        for url in FFMPEG_URLS:
            try:
                self._download(
                    url,
                    archive,
                    progress,
                    "ffmpeg",
                    "Descargando ffmpeg",
                    "ffmpeg",
                )
                downloaded = True
                break
            except ToolCancelled:
                raise
            except Exception as error:  # noqa: BLE001 - se prueba el siguiente espejo
                last_error = error
                logger.warning("Falló la descarga de ffmpeg desde %s: %s", url, error)
                archive.unlink(missing_ok=True)
        if not downloaded:
            if isinstance(last_error, net.CertificateError):
                raise last_error
            raise ToolError(f"No se pudo descargar ffmpeg: {last_error}")

        self._progress(progress, "ffmpeg", 0.9, "Extrayendo ffmpeg...", force=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                found: dict[str, zipfile.ZipInfo] = {}
                for info in bundle.infolist():
                    base = info.filename.rsplit("/", 1)[-1].lower()
                    if base in ("ffmpeg.exe", "ffprobe.exe"):
                        if "/bin/" in info.filename.lower().replace("\\", "/"):
                            found[base] = info
                if len(found) < 2:
                    for info in bundle.infolist():
                        base = info.filename.rsplit("/", 1)[-1].lower()
                        if base in ("ffmpeg.exe", "ffprobe.exe"):
                            found.setdefault(base, info)
                if "ffmpeg.exe" not in found or "ffprobe.exe" not in found:
                    raise ToolError("El archivo de ffmpeg no contiene ffmpeg.exe y ffprobe.exe.")
                for index, name in enumerate(("ffmpeg.exe", "ffprobe.exe")):
                    self._check_cancelled()
                    target = self.bin_dir / name
                    with bundle.open(found[name]) as source, open(target, "wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    self._progress(
                        progress,
                        "ffmpeg",
                        0.95 + index * 0.02,
                        f"Extrayendo {name}...",
                        force=True,
                    )
        finally:
            archive.unlink(missing_ok=True)

        status = self.locate()
        version = self.ffmpeg_version(status)
        probe_ok = status.ffprobe is not None and bool(
            self._run_capture([str(status.ffprobe), "-version"])
        )
        if not version or not probe_ok:
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (self.bin_dir / name).unlink(missing_ok=True)
            raise ToolError("ffmpeg se extrajo pero no se pudo ejecutar.")
        self._progress(progress, "ffmpeg", 1.0, f"ffmpeg {version} instalado", force=True)

    def _download_deno(self, progress: ProgressCallback) -> None:
        if os.name != "nt":
            raise ToolError("La instalación automática de Deno solo está disponible en Windows.")
        machine = platform.machine().lower()
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        url = (
            "https://github.com/denoland/deno/releases/latest/download/"
            f"deno-{arch}-pc-windows-msvc.zip"
        )
        archive = self.bin_dir / "deno-download.zip"
        archive.unlink(missing_ok=True)
        self._download(url, archive, progress, "runtime", "Descargando Deno", "Deno")
        self._progress(progress, "runtime", 0.9, "Extrayendo Deno...", force=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                member = next(
                    (
                        info
                        for info in bundle.infolist()
                        if info.filename.rsplit("/", 1)[-1].lower() == "deno.exe"
                    ),
                    None,
                )
                if member is None:
                    raise ToolError("El archivo de Deno no contiene deno.exe.")
                target = self.bin_dir / "deno.exe"
                with bundle.open(member) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        finally:
            archive.unlink(missing_ok=True)

        version_output = self._run_capture([str(self.bin_dir / "deno.exe"), "--version"])
        if not version_output:
            (self.bin_dir / "deno.exe").unlink(missing_ok=True)
            raise ToolError("Deno se descargó pero no se pudo ejecutar.")
        version = version_output.splitlines()[0].strip()
        self._progress(progress, "runtime", 1.0, f"{version} instalado", force=True)

    def _download(
        self,
        url: str,
        destination: Path,
        progress: ProgressCallback,
        key: str,
        label: str,
        name: str,
    ) -> None:
        self._check_cancelled()
        self._progress(progress, key, 0.0, f"{label}...", force=True)

        def report(done: int, total: int) -> None:
            self._check_cancelled()
            fraction = (done / total) if total else -1.0
            detail = f"{label}: {self._human(done)}"
            if total:
                detail += f" / {self._human(total)}"
            self._progress(progress, key, fraction, detail)

        try:
            net.download(
                url,
                destination,
                name=name,
                on_progress=report,
                check_cancel=self._check_cancelled,
                allow_insecure=self.allow_insecure,
            )
        except net.DownloadError:
            raise
        except OSError as error:
            raise net.DownloadError(f"No se pudo descargar {name}: {error}") from error
        self._progress(progress, key, 1.0, f"{label}: completado", force=True)

    # ---------------------------------------------------------------- utilidad
    def _progress(
        self,
        progress: ProgressCallback,
        key: str,
        fraction: float,
        message: str,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        last = self._last_progress.get(key, 0.0)
        if not force and now - last < 0.12:
            return
        self._last_progress[key] = now
        try:
            progress(key, fraction, message)
        except Exception:  # noqa: BLE001 - la UI no debe romper la descarga
            logger.exception("Falló el callback de progreso")

    @staticmethod
    def _human(value: float) -> str:
        units = ("B", "KB", "MB", "GB")
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{value:.1f} B"
