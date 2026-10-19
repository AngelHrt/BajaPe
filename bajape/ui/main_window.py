"""Ventana principal de BajaPe."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from .. import APP_TAGLINE, __version__, paths
from ..downloader import (
    AUDIO_BITRATE_CHOICES,
    VIDEO_QUALITY_CHOICES,
    DownloadManager,
    DownloadTask,
    TaskOptions,
)
from ..storage import HistoryStore, SettingsStore
from ..tools import ToolManager, ToolStatus
from ..util import (
    format_datetime,
    parse_int,
    parse_urls,
    truncate,
)
from . import theme
from .dialogs import SettingsDialog, SetupDialog, UpdateDialog
from .history_window import HistoryWindow
from .queue_card import QueueCard

logger = logging.getLogger("bajape.ui")

PLACEHOLDER = (
    "Pega aquí uno o varios enlaces de YouTube, uno por línea.\n"
    "Ejemplo: https://www.youtube.com/watch?v=..."
)


class BajaPeApp(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__(fg_color=theme.BG)

        self.settings = SettingsStore()
        self.history = HistoryStore()
        self.tools = ToolManager()
        self._ui_queue: queue.Queue = queue.Queue()
        self._events: queue.Queue = queue.Queue()
        self.manager = DownloadManager(
            self.tools,
            self.history,
            self._enqueue_event,
            max_concurrent=int(self.settings.get("max_concurrent", 3)),
            open_when_done=bool(self.settings.get("open_folder_when_done", False)),
        )

        self._cards: dict[str, QueueCard] = {}
        self._history_window: HistoryWindow | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._update_dialog: UpdateDialog | None = None
        self._setup_dialog: SetupDialog | None = None
        self._tools_ready = False
        self._status_thread_running = False
        self._closing = False
        self._placeholder_active = False
        self._toast_job: str | None = None
        self._error_job: str | None = None
        self._folder_opened = False

        self._dest_dir = str(self.settings.get("download_dir") or paths.default_download_dir())
        self._video_quality = str(self.settings.get("video_quality", "1080"))
        self._audio_bitrate = str(self.settings.get("audio_bitrate", "192"))
        self._video_labels = {label: value for value, label in VIDEO_QUALITY_CHOICES}
        self._audio_labels = {label: value for value, label in AUDIO_BITRATE_CHOICES}

        self.title(f"BajaPe {__version__} - Descargador de YouTube")
        self.minsize(980, 700)
        self._restore_window()
        self._apply_icon()
        self._build_ui()
        self._load_settings()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_ui)
        self.after(60, self._drain_events)
        self.after(200, self._bootstrap)
        self.after(250, self._apply_dark_titlebar)

    # ------------------------------------------------------------- construcción
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 12))
        header.grid_columnconfigure(1, weight=1)

        column = 0
        logo_path = paths.resource_path("assets/logo.png")
        if logo_path.exists():
            try:
                image = Image.open(logo_path)
                self._logo = ctk.CTkImage(light_image=image, dark_image=image, size=(38, 38))
                ctk.CTkLabel(header, text="", image=self._logo).grid(
                    row=0, column=column, rowspan=2, padx=(0, 12)
                )
                column += 1
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo cargar el logo")

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=column, sticky="w")
        ctk.CTkLabel(
            title_box,
            text="BajaPe",
            font=(theme.FONT, 27, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text=APP_TAGLINE,
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=column + 1, sticky="e")
        self._secondary_button(actions, "Historial", self._open_history, width=92).pack(
            side="left", padx=(0, 8)
        )
        self._secondary_button(actions, "Ajustes", self._open_settings, width=84).pack(
            side="left"
        )

        panel = ctk.CTkFrame(
            self,
            fg_color=theme.SURFACE,
            corner_radius=14,
            border_width=1,
            border_color=theme.BORDER,
        )
        panel.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        panel.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(panel, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 6))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            top,
            text="Enlaces de YouTube",
            font=(theme.FONT, 12, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            top,
            text="una URL por línea · video o audio",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        self._secondary_button(top, "Pegar", self._paste_clipboard, width=68, height=28).grid(
            row=0, column=2, sticky="e"
        )

        self.url_box = ctk.CTkTextbox(
            panel,
            height=74,
            corner_radius=10,
            border_width=1,
            border_color=theme.BORDER,
            fg_color=theme.SURFACE_ALT,
            text_color=theme.TEXT,
            font=(theme.FONT, 12),
            wrap="word",
        )
        self.url_box.grid(row=1, column=0, sticky="ew", padx=16)
        self.url_box.bind("<FocusIn>", self._clear_placeholder)
        self.url_box.bind("<FocusOut>", self._show_placeholder)
        self.url_box.bind("<Control-Return>", self._on_ctrl_enter)
        self.url_box.bind("<Control-KP_Enter>", self._on_ctrl_enter)

        options = ctk.CTkFrame(panel, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        options.grid_columnconfigure(7, weight=1)

        self.kind_var = tk.StringVar(value="Video")
        self.kind_segment = ctk.CTkSegmentedButton(
            options,
            values=["Video", "Audio"],
            variable=self.kind_var,
            command=self._on_kind_change,
            width=150,
            height=32,
            corner_radius=8,
            fg_color=theme.SURFACE_ALT,
            selected_color=theme.ACCENT,
            selected_hover_color=theme.ACCENT_HOVER,
            unselected_color=theme.SURFACE_ALT,
            unselected_hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11, "bold"),
        )
        self.kind_segment.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            options,
            text="Calidad",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_MUTED,
        ).grid(row=0, column=1, sticky="e", padx=(16, 6))

        self.quality_var = tk.StringVar()
        self.quality_menu = ctk.CTkOptionMenu(
            options,
            variable=self.quality_var,
            values=[],
            command=self._on_quality_change,
            width=160,
            height=32,
            corner_radius=8,
            fg_color=theme.SURFACE_ALT,
            button_color=theme.SURFACE_HOVER,
            button_hover_color=theme.BORDER,
            dropdown_fg_color=theme.SURFACE_ALT,
            dropdown_hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11),
        )
        self.quality_menu.grid(row=0, column=2, sticky="w")

        self.playlist_var = tk.BooleanVar(
            value=bool(self.settings.get("include_playlists", True))
        )
        self.playlist_switch = ctk.CTkSwitch(
            options,
            text="Playlists/canales",
            variable=self.playlist_var,
            command=self._save_input_options,
            onvalue=True,
            offvalue=False,
            width=150,
            font=(theme.FONT, 11),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
            button_color=theme.TEXT,
            button_hover_color=theme.TEXT,
        )
        self.playlist_switch.grid(row=0, column=3, sticky="w", padx=(20, 0))

        ctk.CTkLabel(
            options,
            text="Máx.",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_MUTED,
        ).grid(row=0, column=4, sticky="e", padx=(16, 5))
        self.limit_var = tk.StringVar(value="0")
        self.limit_entry = ctk.CTkEntry(
            options,
            textvariable=self.limit_var,
            width=56,
            height=32,
            corner_radius=8,
            fg_color=theme.SURFACE_ALT,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11),
        )
        self.limit_entry.grid(row=0, column=5, sticky="w")
        self.limit_entry.bind("<FocusOut>", self._on_limit_focus_out)
        self.limit_entry.bind("<Return>", self._on_limit_focus_out)
        ctk.CTkLabel(
            options,
            text="0 = todos",
            font=(theme.FONT, 9),
            text_color=theme.TEXT_FAINT,
        ).grid(row=0, column=6, sticky="w", padx=(6, 0))

        bottom = ctk.CTkFrame(panel, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 14))
        bottom.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            bottom,
            text="Guardar en",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.folder_label = ctk.CTkLabel(
            bottom,
            text="",
            height=36,
            corner_radius=8,
            fg_color=theme.SURFACE_ALT,
            text_color=theme.TEXT,
            font=(theme.FONT, 10),
            anchor="w",
            padx=10,
        )
        self.folder_label.grid(row=0, column=1, sticky="ew")
        self._secondary_button(bottom, "Cambiar", self._choose_folder, width=82, height=32).grid(
            row=0, column=2, padx=(8, 12)
        )
        self.add_button = ctk.CTkButton(
            bottom,
            text="Añadir a la cola",
            width=170,
            height=36,
            corner_radius=9,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color="#FFFFFF",
            font=(theme.FONT, 12, "bold"),
            command=self._add_to_queue,
        )
        self.add_button.grid(row=0, column=3)

        self.input_error = ctk.CTkLabel(
            panel,
            text="",
            font=(theme.FONT, 10),
            text_color=theme.ERROR,
            anchor="w",
        )
        self.input_error.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 10))

        queue_header = ctk.CTkFrame(self, fg_color="transparent")
        queue_header.grid(row=2, column=0, sticky="ew", padx=24, pady=(2, 8))
        queue_header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            queue_header,
            text="Cola de descargas",
            font=(theme.FONT, 17, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.count_label = ctk.CTkLabel(
            queue_header,
            text="Sin descargas",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        )
        self.count_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.clear_button = ctk.CTkButton(
            queue_header,
            text="Limpiar completadas",
            width=132,
            height=28,
            corner_radius=7,
            fg_color="transparent",
            hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT_MUTED,
            font=(theme.FONT, 10),
            command=self._clear_finished,
            state="disabled",
        )
        self.clear_button.grid(row=0, column=2, sticky="e")

        self.queue_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.SURFACE_HOVER,
        )
        self.queue_frame.grid(row=3, column=0, sticky="nsew", padx=18, pady=(0, 2))

        self.empty_state = ctk.CTkFrame(self.queue_frame, fg_color="transparent")
        self.empty_state.pack(fill="x", pady=48)
        ctk.CTkLabel(
            self.empty_state,
            text="↓",
            font=(theme.FONT, 30, "bold"),
            text_color=theme.BORDER,
        ).pack()
        ctk.CTkLabel(
            self.empty_state,
            text="La cola está vacía",
            font=(theme.FONT, 14, "bold"),
            text_color=theme.TEXT_MUTED,
        ).pack(pady=(2, 2))
        ctk.CTkLabel(
            self.empty_state,
            text="Pega un enlace arriba y pulsa Añadir a la cola.",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_FAINT,
        ).pack()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=24, pady=(2, 14))
        footer.grid_columnconfigure(2, weight=1)

        status_box = ctk.CTkFrame(footer, fg_color="transparent")
        status_box.grid(row=0, column=0, sticky="w")
        self.status_dot = ctk.CTkLabel(
            status_box,
            text="●",
            font=(theme.FONT, 12),
            text_color=theme.TEXT_FAINT,
        )
        self.status_dot.pack(side="left")
        self.status_label = ctk.CTkLabel(
            status_box,
            text="Comprobando componentes...",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        )
        self.status_label.pack(side="left", padx=(4, 0))

        self.toast_label = ctk.CTkLabel(
            footer,
            text="",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_MUTED,
            anchor="e",
        )
        self.toast_label.grid(row=0, column=1, columnspan=2, sticky="e", padx=(10, 10))

        self.update_button = self._secondary_button(
            footer, "Actualizar yt-dlp", self._open_update, width=120, height=29
        )
        self.update_button.grid(row=0, column=3, padx=(0, 8))
        self._secondary_button(
            footer, "Abrir carpeta", self._open_download_folder, width=104, height=29
        ).grid(row=0, column=4)

        self._show_placeholder()
        self._update_counts()

    def _secondary_button(
        self,
        master,
        text: str,
        command,
        width: int = 96,
        height: int = 32,
    ) -> ctk.CTkButton:
        return ctk.CTkButton(
            master,
            text=text,
            width=width,
            height=height,
            corner_radius=8,
            fg_color=theme.SURFACE,
            hover_color=theme.SURFACE_HOVER,
            border_width=1,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11),
            command=command,
        )

    # ------------------------------------------------------------------ arranque
    def _bootstrap(self) -> None:
        threading.Thread(target=self._check_tools, name="bajape-tools", daemon=True).start()

    def _check_tools(self) -> None:
        status = self.tools.locate()
        ytdlp_version = self.tools.ytdlp_version(status) if status.ytdlp else ""
        ffmpeg_version = self.tools.ffmpeg_version(status) if status.ffmpeg else ""
        if status.ready:
            self._post(
                lambda: self._tools_started(status, ytdlp_version, ffmpeg_version)
            )
        else:
            self._post(lambda: self._show_setup(status))

    def _post(self, callback) -> None:
        self._ui_queue.put(callback)

    def _drain_ui(self) -> None:
        if self._closing:
            return
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    logger.exception("Falló una tarea de interfaz")
        except queue.Empty:
            pass
        try:
            self.after(60, self._drain_ui)
        except tk.TclError:
            pass

    def _show_setup(self, status: ToolStatus) -> None:
        if self._closing:
            return
        self.status_dot.configure(text_color=theme.WARNING)
        missing = ", ".join(status.missing) or "componentes"
        self.status_label.configure(text=f"Faltan {missing}")
        if self._setup_dialog is not None and self._setup_dialog.winfo_exists():
            self._setup_dialog.lift()
            return
        self._setup_dialog = SetupDialog(
            self,
            self.tools,
            on_ready=self._on_setup_ready,
            on_exit=self._exit_app,
        )

    def _on_setup_ready(self, status: ToolStatus) -> None:
        self._setup_dialog = None
        if self._closing:
            return
        self._tools_ready = status.ready
        self.manager.start()
        self._refresh_status_async()
        if status.has_js_runtime:
            self._toast("Componentes listos. Ya puedes descargar.", theme.SUCCESS)
        else:
            self._toast("Listo, aunque falta un runtime JS. Revisa los ajustes.", theme.WARNING)

    def _tools_started(
        self, status: ToolStatus, ytdlp_version: str, ffmpeg_version: str
    ) -> None:
        self._tools_ready = status.ready
        self.manager.start()
        self._apply_status(status, ytdlp_version, ffmpeg_version)

    def _refresh_status_async(self) -> None:
        if self._status_thread_running or self._closing:
            return
        self._status_thread_running = True

        def worker() -> None:
            status = self.tools.locate()
            ytdlp_version = self.tools.ytdlp_version(status) if status.ytdlp else ""
            ffmpeg_version = self.tools.ffmpeg_version(status) if status.ffmpeg else ""
            self._post(
                lambda: self._apply_status(status, ytdlp_version, ffmpeg_version)
            )

        threading.Thread(target=worker, name="bajape-status", daemon=True).start()

    def _apply_status(
        self, status: ToolStatus, ytdlp_version: str, ffmpeg_version: str
    ) -> None:
        self._status_thread_running = False
        if self._closing:
            return
        self._tools_ready = status.ready
        if not status.ready:
            self.status_dot.configure(text_color=theme.ERROR)
            self.status_label.configure(text="Faltan componentes para descargar")
            return
        text = f"yt-dlp {ytdlp_version or '?'} · ffmpeg {ffmpeg_version or '?'}"
        if not status.has_js_runtime:
            text += " · sin runtime JS"
            self.status_dot.configure(text_color=theme.WARNING)
        else:
            self.status_dot.configure(text_color=theme.SUCCESS)
        self.status_label.configure(text=text)

    # ------------------------------------------------------------------- ajustes
    def _load_settings(self) -> None:
        self._update_folder_label()
        kind = str(self.settings.get("kind", "video"))
        kind_label = "Audio" if kind == "audio" else "Video"
        self.kind_segment.set(kind_label)
        self.kind_var.set(kind_label)
        self._configure_quality_menu()
        include_playlists = bool(self.settings.get("include_playlists", True))
        self.playlist_var.set(include_playlists)
        if include_playlists:
            self.playlist_switch.select()
        else:
            self.playlist_switch.deselect()
        self.limit_var.set(str(self.settings.get("playlist_limit", 0) or 0))

    def _configure_quality_menu(self) -> None:
        if self.kind_var.get() == "Audio":
            labels = [label for _, label in AUDIO_BITRATE_CHOICES]
            current = next(
                (label for value, label in AUDIO_BITRATE_CHOICES if value == self._audio_bitrate),
                labels[2],
            )
        else:
            labels = [label for _, label in VIDEO_QUALITY_CHOICES]
            current = next(
                (label for value, label in VIDEO_QUALITY_CHOICES if value == self._video_quality),
                labels[3],
            )
        self.quality_menu.configure(values=labels)
        self.quality_var.set(current)

    def _on_kind_change(self, value: str) -> None:
        self._configure_quality_menu()
        self._save_input_options()

    def _on_quality_change(self, label: str) -> None:
        if self.kind_var.get() == "Audio":
            self._audio_bitrate = self._audio_labels.get(label, "192")
        else:
            self._video_quality = self._video_labels.get(label, "1080")
        self._save_input_options()

    def _on_limit_focus_out(self, event=None) -> None:
        value = max(0, min(10000, parse_int(self.limit_var.get(), 0)))
        self.limit_var.set(str(value))
        self._save_input_options()

    def _save_input_options(self) -> None:
        if self._closing:
            return
        try:
            self.settings.update(
                {
                    "kind": "audio" if self.kind_var.get() == "Audio" else "video",
                    "video_quality": self._video_quality,
                    "audio_bitrate": self._audio_bitrate,
                    "include_playlists": bool(self.playlist_var.get()),
                    "playlist_limit": max(0, min(10000, parse_int(self.limit_var.get(), 0))),
                    "download_dir": self._dest_dir,
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron guardar los ajustes de entrada")

    def _update_folder_label(self) -> None:
        self.folder_label.configure(text=truncate(self._dest_dir, 96))

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self,
            initialdir=self._dest_dir if Path(self._dest_dir).is_dir() else str(Path.home()),
            title="Elegir carpeta de descargas",
        )
        if not chosen:
            return
        self._dest_dir = chosen
        self.settings.set("download_dir", chosen)
        self._update_folder_label()

    def _open_download_folder(self) -> None:
        folder = Path(self._dest_dir)
        if not folder.is_dir():
            folder = paths.default_download_dir()
            self._dest_dir = str(folder)
            self._update_folder_label()
        paths.open_path(folder)

    def _open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.lift()
            return
        self._settings_dialog = SettingsDialog(
            self, self.settings, self.manager, self._on_settings_saved
        )

    def _on_settings_saved(self) -> None:
        self._toast("Ajustes guardados", theme.SUCCESS)

    def _open_history(self) -> None:
        if self._history_window is not None and self._history_window.winfo_exists():
            self._history_window.lift()
            self._history_window.refresh()
            return
        self._history_window = HistoryWindow(
            self, self.history, self._on_history_closed
        )

    def _on_history_closed(self) -> None:
        self._history_window = None

    def _open_update(self) -> None:
        if not self._tools_ready:
            messagebox.showinfo(
                "BajaPe",
                "Primero deben estar listos yt-dlp y ffmpeg.",
                parent=self,
            )
            return
        if self.manager.active_count > 0:
            messagebox.showwarning(
                "Actualizar yt-dlp",
                "Espera a que terminen las descargas en curso antes de actualizar.",
                parent=self,
            )
            return
        if self._update_dialog is not None and self._update_dialog.winfo_exists():
            self._update_dialog.lift()
            return
        self._update_dialog = UpdateDialog(self, self.tools, self._on_update_done)

    def _on_update_done(self, version: str) -> None:
        self._toast(f"yt-dlp {version} instalado", theme.SUCCESS)
        self._refresh_status_async()

    # ------------------------------------------------------------------ entradas
    def _clear_placeholder(self, event=None) -> None:
        if self._placeholder_active:
            self.url_box.delete("1.0", "end")
            self.url_box.configure(text_color=theme.TEXT)
            self._placeholder_active = False

    def _show_placeholder(self, event=None) -> None:
        if self._placeholder_active or self._closing:
            return
        if self.url_box.get("1.0", "end-1c").strip():
            return
        self.url_box.insert("1.0", PLACEHOLDER)
        self.url_box.configure(text_color=theme.TEXT_FAINT)
        self._placeholder_active = True

    def _get_input_text(self) -> str:
        if self._placeholder_active:
            return ""
        return self.url_box.get("1.0", "end-1c")

    def _on_ctrl_enter(self, event=None) -> str:
        self._add_to_queue()
        return "break"

    def _paste_clipboard(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        self._clear_placeholder()
        existing = self.url_box.get("1.0", "end-1c").strip()
        if existing:
            self.url_box.insert("end", "\n" + text.strip())
        else:
            self.url_box.insert("1.0", text.strip())
        self.url_box.focus_set()

    def _add_to_queue(self) -> None:
        if self._closing:
            return
        if not self._tools_ready:
            self._show_input_error(
                "Los componentes aún se están preparando. Espera un momento."
            )
            return
        urls = parse_urls(self._get_input_text())
        if not urls:
            self._show_input_error("Pega al menos un enlace válido de YouTube.")
            return

        folder = Path(self._dest_dir)
        if not folder.is_dir():
            folder = paths.default_download_dir()
            self._dest_dir = str(folder)
            self._update_folder_label()
            self.settings.set("download_dir", self._dest_dir)

        kind = "audio" if self.kind_var.get() == "Audio" else "video"
        limit = max(0, min(10000, parse_int(self.limit_var.get(), 0)))
        self.limit_var.set(str(limit))
        options = TaskOptions(
            kind=kind,
            video_quality=self._video_quality,
            audio_bitrate=self._audio_bitrate,
            include_playlist=bool(self.playlist_var.get()),
            playlist_limit=limit if self.playlist_var.get() else 0,
            cookies_browser=str(self.settings.get("cookies_browser", "")),
        )
        self.manager.add_tasks(urls, options, folder)
        self._folder_opened = False
        self._save_input_options()

        self.url_box.delete("1.0", "end")
        self._placeholder_active = False
        self.url_box.configure(text_color=theme.TEXT)
        self._hide_input_error()
        count = len(urls)
        self._toast(
            f"{count} descarga{'s' if count != 1 else ''} añadida{'s' if count != 1 else ''} a la cola",
            theme.SUCCESS,
        )

    def _show_input_error(self, message: str) -> None:
        self.input_error.configure(text=message)
        if self._error_job is not None:
            try:
                self.after_cancel(self._error_job)
            except tk.TclError:
                pass
        self._error_job = self.after(6000, self._hide_input_error)

    def _hide_input_error(self) -> None:
        self._error_job = None
        try:
            self.input_error.configure(text="")
        except tk.TclError:
            pass

    # --------------------------------------------------------------------- cola
    def _enqueue_event(self, kind: str, task: DownloadTask) -> None:
        self._events.put((kind, task))

    def _drain_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, task = self._events.get_nowait()
                self._handle_event(kind, task)
        except queue.Empty:
            pass
        try:
            self.after(60, self._drain_events)
        except tk.TclError:
            pass

    def _handle_event(self, kind: str, task: DownloadTask) -> None:
        if self._closing:
            return
        if kind == "added":
            self._ensure_card(task)
        elif kind == "updated":
            card = self._cards.get(task.id)
            if card is not None:
                card.update_task(task)
        elif kind == "finished":
            card = self._cards.get(task.id)
            if card is not None:
                card.update_task(task)
            self._on_task_finished(task)
        elif kind == "removed":
            card = self._cards.pop(task.id, None)
            if card is not None:
                card.destroy()
        self._toggle_empty_state()
        self._update_counts()

    def _ensure_card(self, task: DownloadTask) -> None:
        if task.id in self._cards:
            return
        self.empty_state.pack_forget()
        card = QueueCard(
            self.queue_frame,
            task,
            on_cancel=self._cancel_task,
            on_retry=self._retry_task,
            on_remove=self._remove_task,
            on_open=self._open_task,
        )
        card.pack(fill="x", padx=6, pady=(0, 8))
        self._cards[task.id] = card

    def _toggle_empty_state(self) -> None:
        if self._cards:
            self.empty_state.pack_forget()
        else:
            self.empty_state.pack(fill="x", pady=48)

    def _update_counts(self) -> None:
        active = self.manager.active_count
        queued = self.manager.queued_count
        tasks = self.manager.ordered_tasks()
        finished = sum(1 for task in tasks if task.is_terminal)
        parts: list[str] = []
        if active:
            parts.append(f"{active} en curso")
        if queued:
            parts.append(f"{queued} en espera")
        if finished and (active or queued):
            parts.append(f"{finished} finalizada{'s' if finished != 1 else ''}")
        elif finished and not parts:
            parts.append(f"{finished} descarga{'s' if finished != 1 else ''}")
        self.count_label.configure(text=" · ".join(parts) if parts else "Sin descargas")
        self.clear_button.configure(state="normal" if finished else "disabled")

    def _on_task_finished(self, task: DownloadTask) -> None:
        title = truncate(task.current_item or task.display_title, 42)
        if task.status == "done":
            self._toast(f"Listo: {title}", theme.SUCCESS)
            if self._history_window is not None and self._history_window.winfo_exists():
                self._history_window.refresh()
            self._schedule_open_folder()
        elif task.status == "partial":
            self._toast(f"Con errores: {title}", theme.WARNING)
            if self._history_window is not None and self._history_window.winfo_exists():
                self._history_window.refresh()
        elif task.status == "error":
            self._toast(f"Error: {title}", theme.ERROR)

    def _schedule_open_folder(self) -> None:
        if not self.settings.get("open_folder_when_done", False):
            return
        self.after(500, self._open_folder_if_idle)

    def _open_folder_if_idle(self) -> None:
        if self._closing or self._folder_opened:
            return
        if self.manager.active_count > 0 or self.manager.queued_count > 0:
            self.after(400, self._open_folder_if_idle)
            return
        self._folder_opened = True
        self._open_download_folder()

    def _cancel_task(self, task_id: str) -> None:
        self.manager.cancel(task_id)

    def _retry_task(self, task_id: str) -> None:
        self.manager.retry(task_id)
        self._folder_opened = False

    def _remove_task(self, task_id: str) -> None:
        self.manager.remove(task_id)

    def _open_task(self, task_id: str) -> None:
        task = self.manager.get(task_id)
        if task is None:
            return
        if task.filepath and Path(task.filepath).exists():
            paths.reveal_path(task.filepath)
        else:
            paths.open_path(task.dest_dir)

    def _clear_finished(self) -> None:
        self.manager.clear_finished()

    # ------------------------------------------------------------------- variados
    def _toast(self, message: str, color: str = theme.TEXT_MUTED) -> None:
        if self._closing:
            return
        self.toast_label.configure(text=truncate(message, 70), text_color=color)
        if self._toast_job is not None:
            try:
                self.after_cancel(self._toast_job)
            except tk.TclError:
                pass
        self._toast_job = self.after(
            5000, lambda: self.toast_label.configure(text="")
        )

    def _restore_window(self) -> None:
        import re

        geometry = str(self.settings.get("window_geometry", "") or "")
        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
        valid = bool(match and int(match.group(1)) >= 980 and int(match.group(2)) >= 700)
        if valid:
            self.geometry(geometry)
        else:
            self.geometry("1120x820")
            self.after(30, self._center_window)

    def _center_window(self) -> None:
        self.update_idletasks()
        width = 1120
        height = 820
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(width, screen_width - 40)
        height = min(height, screen_height - 80)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _apply_icon(self) -> None:
        icon = paths.resource_path("assets/bajape.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                logger.exception("No se pudo aplicar el icono")

    def _apply_dark_titlebar(self) -> None:
        if os.name != "nt":
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
                )
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo activar la barra de título oscura", exc_info=True)

    # --------------------------------------------------------------------- salir
    def _save_window_state(self) -> None:
        try:
            self.settings.update(
                {
                    "window_geometry": self.geometry(),
                    "download_dir": self._dest_dir,
                    "kind": "audio" if self.kind_var.get() == "Audio" else "video",
                    "video_quality": self._video_quality,
                    "audio_bitrate": self._audio_bitrate,
                    "include_playlists": bool(self.playlist_var.get()),
                    "playlist_limit": max(0, min(10000, parse_int(self.limit_var.get(), 0))),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo guardar el estado de la ventana")

    def _exit_app(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.tools.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.manager.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        if self._closing:
            return
        active = self.manager.active_count
        queued = self.manager.queued_count
        if active or queued:
            confirmed = messagebox.askyesno(
                "Salir de BajaPe",
                "Hay descargas en curso o en espera.\n¿Cancelarlas y salir?",
                parent=self,
            )
            if not confirmed:
                return
        self._save_window_state()
        self._exit_app()
