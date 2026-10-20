"""Motor de descargas basado en el ejecutable de yt-dlp."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from . import paths
from .storage import HistoryStore
from .tools import ToolError, ToolManager
from .util import (
    friendly_error,
    human_bytes,
    human_eta,
    human_speed,
    is_playlist_url,
    parse_float,
    parse_int,
)

logger = logging.getLogger("bajape.downloader")

TaskStatus = Literal[
    "queued",
    "preparing",
    "downloading",
    "processing",
    "done",
    "partial",
    "error",
    "cancelled",
    "cancelling",
]

ACTIVE_STATUSES = {"preparing", "downloading", "processing", "cancelling"}
TERMINAL_STATUSES = {"done", "partial", "error", "cancelled"}

VIDEO_QUALITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("best", "Mejor disponible"),
    ("2160", "2160p (4K)"),
    ("1440", "1440p (2K)"),
    ("1080", "1080p (Full HD)"),
    ("720", "720p (HD)"),
    ("480", "480p"),
    ("360", "360p"),
)

AUDIO_BITRATE_CHOICES: tuple[tuple[str, str], ...] = (
    ("320", "320 kbps"),
    ("256", "256 kbps"),
    ("192", "192 kbps"),
    ("128", "128 kbps"),
)

COOKIE_BROWSER_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "No usar"),
    ("chrome", "Chrome"),
    ("edge", "Edge"),
    ("firefox", "Firefox"),
    ("brave", "Brave"),
)

PROGRESS_TEMPLATE = (
    "download:BP|%(progress.status)s|%(progress.downloaded_bytes)s|"
    "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
    "%(progress.speed)s|%(progress.eta)s|%(info.playlist_index)s|"
    "%(info.n_entries)s|%(info.title)s"
)


@dataclass
class TaskOptions:
    kind: str = "video"
    video_quality: str = "1080"
    audio_bitrate: str = "192"
    include_playlist: bool = True
    playlist_limit: int = 0
    cookies_browser: str = ""

    def quality_label(self) -> str:
        if self.kind == "audio":
            return f"MP3 {self.audio_bitrate} kbps"
        if self.video_quality == "best":
            return "Mejor disponible"
        return f"{self.video_quality}p"


@dataclass
class DownloadTask:
    id: str
    url: str
    options: TaskOptions
    dest_dir: str
    title: str = ""
    current_item: str = ""
    status: TaskStatus = "queued"
    progress: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    total: int = 0
    playlist_index: int = 0
    playlist_count: int = 0
    error: str = ""
    filepath: str = ""
    temp_dir: str = ""
    saved_count: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    is_playlist: bool = False
    _phase: int = field(default=0, repr=False)
    _phase_count: int = field(default=2, repr=False)
    _last_downloaded: int = field(default=-1, repr=False)
    _last_emit: float = field(default=0.0, repr=False)

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def display_title(self) -> str:
        return self.title or "Analizando enlace..."

    def detail_text(self) -> str:
        if self.status == "error":
            return self.error or "Error desconocido"
        if self.status == "cancelled":
            return "Descarga cancelada"
        if self.status == "done":
            return "Descarga completada"
        if self.status == "partial":
            return self.error or "Completado con errores"
        if self.status == "cancelling":
            return "Cancelando..."
        if self.status == "processing":
            return "Procesando archivo..."
        if self.status == "queued":
            return "En espera"
        if self.status == "preparing":
            return "Preparando descarga..."
        parts: list[str] = []
        if self.playlist_count > 1 and self.playlist_index:
            parts.append(f"Elemento {self.playlist_index}/{self._effective_count()}")
        if self.progress:
            parts.append(f"{self.progress:.0f}%")
        speed = human_speed(self.speed)
        if speed:
            parts.append(speed)
        eta = human_eta(self.eta)
        if eta:
            parts.append(f"quedan {eta}")
        if self.total:
            parts.append(human_bytes(self.total))
        if self.is_playlist and self.current_item:
            parts.append(self.current_item)
        return " · ".join(parts) if parts else "Descargando..."

    def _effective_count(self) -> int:
        count = self.playlist_count
        if count <= 0:
            return 0
        limit = self.options.playlist_limit
        if limit > 0:
            return min(count, limit)
        return count


class DownloadManager:
    """Cola thread-safe con varios trabajadores y eventos hacia la interfaz."""

    def __init__(
        self,
        tools: ToolManager,
        history: HistoryStore,
        on_event: Callable[[str, DownloadTask], None],
        max_concurrent: int = 3,
        open_when_done: bool = False,
    ) -> None:
        self.tools = tools
        self.history = history
        self.on_event = on_event
        self.max_concurrent = max(1, min(4, int(max_concurrent)))
        self.open_when_done = open_when_done
        self._lock = threading.RLock()
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._cancel_ids: set[str] = set()
        self._workers: list[threading.Thread] = []
        self._started = False
        self._stopping = False

    # ------------------------------------------------------------------ cola
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopping = False
        self._ensure_workers()

    def set_max_concurrent(self, value: int) -> None:
        with self._lock:
            self.max_concurrent = max(1, min(4, int(value)))
        if self._started:
            self._ensure_workers()

    def _ensure_workers(self) -> None:
        with self._lock:
            self._workers = [worker for worker in self._workers if worker.is_alive()]
            while len(self._workers) < self.max_concurrent:
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"bajape-download-{len(self._workers) + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def _worker_count(self) -> int:
        return sum(1 for worker in self._workers if worker.is_alive())

    def _worker_loop(self) -> None:
        while not self._stopping:
            with self._lock:
                if self._worker_count() > self.max_concurrent:
                    return
            try:
                task_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if task_id is None:
                    return
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is None or task.status != "queued":
                        continue
                self._run_task(task)
            finally:
                self._queue.task_done()

    def add_tasks(
        self,
        urls: list[str],
        options: TaskOptions,
        dest_dir: str | Path,
    ) -> list[DownloadTask]:
        destination = Path(dest_dir)
        destination.mkdir(parents=True, exist_ok=True)
        created: list[DownloadTask] = []
        for url in urls:
            task = DownloadTask(
                id=uuid.uuid4().hex[:10],
                url=url,
                options=TaskOptions(**vars(options)),
                dest_dir=str(destination),
                title="Analizando enlace...",
                is_playlist=options.include_playlist and is_playlist_url(url),
                _phase_count=2 if options.kind == "video" else 1,
            )
            with self._lock:
                self._tasks[task.id] = task
                self._order.append(task.id)
            self._emit("added", task, force=True)
            self._queue.put(task.id)
            created.append(task)
        self._ensure_workers()
        return created

    def get(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def ordered_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return [self._tasks[task_id] for task_id in self._order if task_id in self._tasks]

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for task in self._tasks.values() if task.is_active)

    @property
    def queued_count(self) -> int:
        with self._lock:
            return sum(1 for task in self._tasks.values() if task.status == "queued")

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.is_terminal:
                return
            self._cancel_ids.add(task_id)
            if task.status == "queued":
                task.status = "cancelled"
                task.finished_at = time.time()
                task.progress = 0.0
                self._emit("finished", task, force=True)
                return
            if task.status != "cancelling":
                task.status = "cancelling"
                self._emit("updated", task, force=True)
            process = self._procs.get(task_id)
        if process is not None:
            self._terminate(process)
        # Si aún no hay proceso, el runner comprobará la cancelación antes de lanzarlo.

    def retry(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not task.is_terminal:
                return
            self._cancel_ids.discard(task_id)
            task.status = "queued"
            task.progress = 0.0
            task.speed = 0.0
            task.eta = 0.0
            task.total = 0
            task.error = ""
            task.filepath = ""
            task.saved_count = 0
            task.playlist_index = 0
            task.playlist_count = 0
            task.current_item = ""
            task._phase = 0
            task._phase_count = 2 if task.options.kind == "video" else 1
            task._last_downloaded = -1
            task.started_at = 0.0
            task.finished_at = 0.0
            if not task.title or task.title == "Analizando enlace...":
                task.title = "Analizando enlace..."
        self._emit("updated", task, force=True)
        self._queue.put(task_id)
        self._ensure_workers()

    def remove(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.is_active:
                return False
            self._tasks.pop(task_id, None)
            if task_id in self._order:
                self._order.remove(task_id)
        self._emit("removed", task, force=True)
        return True

    def clear_finished(self) -> int:
        with self._lock:
            removable = [
                task_id
                for task_id, task in self._tasks.items()
                if task.is_terminal and task.status != "cancelling"
            ]
        count = 0
        for task_id in removable:
            if self.remove(task_id):
                count += 1
        return count

    def shutdown(self) -> None:
        self._stopping = True
        with self._lock:
            running = [task_id for task_id, task in self._tasks.items() if task.is_active]
        for task_id in running:
            self.cancel(task_id)
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=2.0)

    # -------------------------------------------------------------- ejecución
    def _run_task(self, task: DownloadTask) -> None:
        with self._lock:
            if task.id in self._cancel_ids:
                task.status = "cancelled"
                task.finished_at = time.time()
                self._emit("finished", task, force=True)
                return
            task.status = "preparing"
            task.error = ""
            task.progress = 0.0
            task.speed = 0.0
            task.eta = 0.0
            task.total = 0
            task.started_at = time.time()
            task._last_downloaded = -1
            task._phase = 0
        self._emit("updated", task, force=True)

        try:
            command = self._build_command(task)
        except (ToolError, OSError) as error:
            self._fail(task, str(error))
            return

        logger.info("Ejecutando yt-dlp para %s", task.url)
        logger.debug("Comando: %s", command)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=task.dest_dir,
                env=self.tools.environment(),
                creationflags=paths.CREATE_NO_WINDOW_GROUP,
            )
        except OSError as error:
            self._fail(task, f"No se pudo iniciar yt-dlp: {error}")
            return

        with self._lock:
            self._procs[task.id] = process

        last_error = ""
        saved_here = 0
        current_title = ""
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    if not line:
                        continue
                    if line.startswith("BP_TITLE|"):
                        current_title = self._handle_title(task, line)
                    elif line.startswith("BP|"):
                        self._handle_progress(task, line)
                    elif line.startswith("BP_FILE|"):
                        filepath = line.split("|", 1)[1].strip()
                        if filepath and filepath != "NA":
                            saved_here += 1
                            self._handle_file(task, filepath, current_title)
                    elif line.startswith("ERROR:"):
                        last_error = line
                        logger.warning("yt-dlp: %s", line)
            return_code = process.wait()
        except Exception as error:  # noqa: BLE001 - nunca debe morir el worker
            logger.exception("Error leyendo la salida de yt-dlp")
            last_error = last_error or str(error)
            return_code = -1
        finally:
            with self._lock:
                self._procs.pop(task.id, None)
            self._cleanup_temp(task)

        cancelled = task.id in self._cancel_ids or self._stopping
        with self._lock:
            task.finished_at = time.time()
            task.speed = 0.0
            task.eta = 0.0
            if cancelled:
                task.status = "cancelled"
                task.progress = min(task.progress, 99.0)
                task.error = ""
            elif return_code == 0:
                if task.saved_count > 0 or not last_error:
                    task.status = "done"
                    task.progress = 100.0
                    task.error = ""
                else:
                    task.status = "error"
                    task.error = friendly_error(last_error)
            elif task.saved_count > 0:
                task.status = "partial"
                task.progress = 100.0
                task.error = (
                    friendly_error(last_error)
                    if last_error
                    else f"yt-dlp terminó con código {return_code}"
                )
            else:
                task.status = "error"
                task.error = (
                    friendly_error(last_error)
                    if last_error
                    else f"yt-dlp terminó con código {return_code}"
                )
                logger.error("Falló la descarga %s: %s", task.url, task.error)
        self._emit("finished", task, force=True)

    def _fail(self, task: DownloadTask, message: str) -> None:
        with self._lock:
            task.status = "error"
            task.error = message
            task.finished_at = time.time()
        self._cleanup_temp(task)
        self._emit("finished", task, force=True)

    def _handle_title(self, task: DownloadTask, line: str) -> str:
        parts = line.split("|", 3)
        if len(parts) < 4:
            return ""
        index = parse_int(parts[1])
        count = parse_int(parts[2])
        title = parts[3].strip()
        with self._lock:
            if index > 0:
                task.playlist_index = index
            if count > 0:
                task.playlist_count = count
            if title and title != "NA":
                task.current_item = title
                effective = task._effective_count()
                if task.is_playlist or effective > 1:
                    task.title = (
                        f"Playlist o canal · {effective} elementos"
                        if effective > 1
                        else "Playlist o canal"
                    )
                elif not task.title or task.title == "Analizando enlace...":
                    task.title = title
        self._emit("updated", task, force=True)
        return title if title != "NA" else ""

    def _handle_progress(self, task: DownloadTask, line: str) -> None:
        parts = line.split("|", 9)
        if len(parts) < 10:
            return
        status = parts[1]
        downloaded = parse_int(parts[2])
        total_bytes = parse_int(parts[3])
        estimated = parse_int(parts[4])
        speed = parse_float(parts[5])
        eta = parse_float(parts[6])
        index = parse_int(parts[7])
        count = parse_int(parts[8])
        title = parts[9].strip()

        with self._lock:
            if index > 0:
                task.playlist_index = index
            if count > 0:
                task.playlist_count = count
            if title and title != "NA" and task.is_playlist:
                task.current_item = title

            if status == "downloading":
                if task._last_downloaded >= 0 and downloaded + 1024 < task._last_downloaded:
                    task._phase = min(task._phase + 1, task._phase_count - 1)
                task._last_downloaded = downloaded
                task.status = "downloading"
                task.speed = speed
                task.eta = eta
                task.total = total_bytes or estimated
                fraction = (downloaded / task.total) if task.total > 0 else 0.0
                fraction = max(0.0, min(1.0, fraction))
                task.progress = self._overall_progress(task, fraction)
            elif status == "finished":
                task.speed = 0.0
                task.eta = 0.0
                if task._phase_count == 1:
                    task.progress = max(task.progress, self._item_complete_progress(task))
                elif task._phase >= task._phase_count - 1:
                    task.progress = max(task.progress, self._item_complete_progress(task))

        self._emit("updated", task)

    def _handle_file(self, task: DownloadTask, filepath: str, title: str) -> None:
        path = Path(filepath)
        with self._lock:
            task.filepath = filepath
            task.saved_count += 1
            effective = task._effective_count()
            if effective and task.playlist_index and task.playlist_index < effective:
                task.status = "downloading"
                task.progress = max(
                    task.progress, (task.playlist_index / effective) * 100.0
                )
            else:
                task.status = "processing"
                task.progress = min(99.0, max(task.progress, self._item_complete_progress(task)))
        try:
            self.history.add(
                title=title or path.stem,
                url=task.url,
                path=filepath,
                kind=task.options.kind,
                quality=task.options.quality_label(),
            )
        except Exception:  # noqa: BLE001 - el historial no debe romper la descarga
            logger.exception("No se pudo guardar la entrada del historial")
        self._emit("updated", task, force=True)

    def _item_complete_progress(self, task: DownloadTask) -> float:
        effective = task._effective_count()
        if effective > 1 and task.playlist_index:
            if task.playlist_index >= effective:
                return 99.0
            return (task.playlist_index / effective) * 100.0
        return 99.0

    def _overall_progress(self, task: DownloadTask, fraction: float) -> float:
        phase_count = max(1, task._phase_count)
        phase_progress = (min(task._phase, phase_count - 1) + fraction) / phase_count
        effective = task._effective_count()
        if effective > 1 and task.playlist_index:
            overall = ((task.playlist_index - 1) + phase_progress) / effective
        else:
            overall = phase_progress
        return max(0.0, min(99.0, overall * 100.0))

    # --------------------------------------------------------------- comandos
    def _build_command(self, task: DownloadTask) -> list[str]:
        status = self.tools.locate()
        if status.ytdlp is None or status.ffmpeg is None:
            raise ToolError("Faltan yt-dlp o ffmpeg. Abre la aplicación de nuevo para instalarlos.")

        temp_dir = Path(task.dest_dir) / ".bajape-tmp" / task.id
        temp_dir.mkdir(parents=True, exist_ok=True)
        task.temp_dir = str(temp_dir)

        command: list[str] = [
            str(status.ytdlp),
            "--ignore-config",
            "--newline",
            "--no-colors",
            "--no-warnings",
            "--progress",
            "--progress-template",
            PROGRESS_TEMPLATE,
            "--print",
            "before_dl:BP_TITLE|%(playlist_index)s|%(n_entries)s|%(title)s",
            "--print",
            "after_move:BP_FILE|%(filepath)s",
            "--windows-filenames",
            "--trim-filenames",
            "180",
            "--no-overwrites",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--concurrent-fragments",
            "4",
            "--socket-timeout",
            "20",
            "--paths",
            f"home:{task.dest_dir}",
            "--paths",
            f"temp:{temp_dir}",
            "--output",
            "%(title)s [%(id)s].%(ext)s",
            "--ffmpeg-location",
            str(status.ffmpeg.parent),
        ]
        command += self.tools.js_args(status)
        command += self.tools.tls_args()

        options = task.options
        if options.include_playlist:
            command.append("--yes-playlist")
            if options.playlist_limit > 0:
                command += ["--playlist-end", str(options.playlist_limit)]
        else:
            command.append("--no-playlist")

        if options.cookies_browser:
            command += ["--cookies-from-browser", options.cookies_browser]

        if options.kind == "audio":
            command += [
                "-f",
                "ba/b",
                "-x",
                "--audio-format",
                "mp3",
                "--audio-quality",
                f"{options.audio_bitrate}K",
            ]
        else:
            quality = options.video_quality
            if quality == "best":
                selector = "bv*+ba[ext=m4a]/bv*+ba/b"
            else:
                height = int(quality)
                if height <= 1080:
                    selector = (
                        f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]"
                        f"/bv*[height<={height}]+ba/b[height<={height}]"
                    )
                else:
                    selector = (
                        f"bv*[height<={height}]+ba[ext=m4a]"
                        f"/bv*[height<={height}]+ba/b[height<={height}]"
                    )
            command += ["-f", selector, "--merge-output-format", "mp4"]

        command += ["--", task.url]
        return command

    # ---------------------------------------------------------------- eventos
    def _emit(self, kind: str, task: DownloadTask, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - task._last_emit < 0.2:
            return
        task._last_emit = now
        try:
            self.on_event(kind, task)
        except Exception:  # noqa: BLE001 - la UI no debe tumbar el worker
            logger.exception("Falló el callback de eventos")

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    creationflags=paths.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _cleanup_temp(task: DownloadTask) -> None:
        if not task.temp_dir:
            return
        temp_dir = Path(task.temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
        parent = temp_dir.parent
        if parent.name == ".bajape-tmp":
            try:
                parent.rmdir()
            except OSError:
                pass
        task.temp_dir = ""
