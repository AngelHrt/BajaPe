"""Diálogos de preparación, actualización y ajustes."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from ..downloader import COOKIE_BROWSER_CHOICES, DownloadManager
from ..storage import SettingsStore
from ..tools import ToolCancelled, ToolManager, ToolStatus
from . import theme


def center_on_parent(window: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    window.update_idletasks()
    try:
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    except tk.TclError:
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 2
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


def make_modal(window: tk.Toplevel) -> None:
    def _grab() -> None:
        try:
            window.grab_set()
            window.lift()
            window.focus_force()
        except tk.TclError:
            pass

    window.after(80, _grab)


class SetupDialog(ctk.CTkToplevel):
    """Primera ejecución: descarga yt-dlp, ffmpeg y un runtime de JavaScript."""

    STEPS = (
        ("ytdlp", "yt-dlp", "Motor de descargas"),
        ("ffmpeg", "ffmpeg", "Unión de video y audio, conversión a MP3"),
        ("runtime", "Runtime JS", "Necesario para YouTube (Deno o Node.js)"),
    )

    def __init__(
        self,
        master: tk.Misc,
        tools: ToolManager,
        on_ready: Callable[[ToolStatus], None],
        on_exit: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.tools = tools
        self.on_ready = on_ready
        self.on_exit = on_exit
        self._messages: queue.Queue[tuple] = queue.Queue()
        self._running = False
        self._done = False
        self._closing = False
        self._status: ToolStatus | None = None
        self._bars: dict[str, ctk.CTkProgressBar] = {}
        self._details: dict[str, ctk.CTkLabel] = {}
        self._started: dict[str, bool] = {}

        self.title("BajaPe - Preparación inicial")
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)
        self._build()
        center_on_parent(self, master, 660, 500)
        make_modal(self)
        self.protocol("WM_DELETE_WINDOW", self._abort)
        self.after(120, self._start)
        self.after(80, self._poll)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 8))
        ctk.CTkLabel(
            header,
            text="BAJAPE",
            font=(theme.FONT, 11, "bold"),
            text_color=theme.ACCENT,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Preparando la aplicación",
            font=(theme.FONT, 23, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(
            header,
            text=(
                "Solo hace falta la primera vez. Se descargan las herramientas "
                "necesarias y se guardan en tu carpeta de usuario."
            ),
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=590,
        ).pack(anchor="w", pady=(6, 0))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=26, pady=(4, 0))
        content.grid_columnconfigure(0, weight=1)

        for index, (key, name, subtitle) in enumerate(self.STEPS):
            card = ctk.CTkFrame(
                content,
                fg_color=theme.SURFACE,
                corner_radius=12,
                border_width=1,
                border_color=theme.BORDER,
            )
            card.grid(row=index, column=0, sticky="ew", pady=(0, 8))
            card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                card,
                text=name,
                font=(theme.FONT, 12, "bold"),
                text_color=theme.TEXT,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=(14, 10), pady=(10, 0))
            detail = ctk.CTkLabel(
                card,
                text=subtitle,
                font=(theme.FONT, 10),
                text_color=theme.TEXT_MUTED,
                anchor="e",
            )
            detail.grid(row=0, column=1, sticky="e", padx=(0, 14), pady=(10, 0))
            bar = ctk.CTkProgressBar(
                card,
                height=7,
                corner_radius=4,
                fg_color=theme.SURFACE_HOVER,
                progress_color=theme.ACCENT,
            )
            bar.set(0)
            bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 12))
            self._bars[key] = bar
            self._details[key] = detail
            self._started[key] = False

        self.message_label = ctk.CTkLabel(
            content,
            text="",
            font=(theme.FONT, 11),
            text_color=theme.ERROR,
            anchor="w",
            justify="left",
            wraplength=590,
        )
        self.message_label.grid(row=10, column=0, sticky="ew", pady=(4, 0))

        self.note_label = ctk.CTkLabel(
            content,
            text="Descarga aproximada: yt-dlp 17 MB + ffmpeg 110 MB. Puede tardar unos minutos.",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_FAINT,
            anchor="w",
            justify="left",
            wraplength=590,
        )
        self.note_label.grid(row=11, column=0, sticky="ew", pady=(6, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=26, pady=(10, 20))
        footer.grid_columnconfigure(0, weight=1)

        self.retry_button = ctk.CTkButton(
            footer,
            text="Reintentar",
            width=110,
            height=34,
            fg_color=theme.SURFACE_HOVER,
            hover_color=theme.BORDER,
            text_color=theme.TEXT,
            font=(theme.FONT, 12),
            command=self._retry,
        )
        self.retry_button.grid(row=0, column=1, padx=(0, 8))
        self.retry_button.grid_remove()

        self.exit_button = ctk.CTkButton(
            footer,
            text="Salir",
            width=90,
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color=theme.BORDER,
            hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT_MUTED,
            font=(theme.FONT, 12),
            command=self._abort,
        )
        self.exit_button.grid(row=0, column=2)

        self.continue_button = ctk.CTkButton(
            footer,
            text="Continuar",
            width=130,
            height=34,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color="#FFFFFF",
            font=(theme.FONT, 12, "bold"),
            command=self._continue,
        )
        self.continue_button.grid(row=0, column=3)
        self.continue_button.grid_remove()

    # ------------------------------------------------------------------ proceso
    def _start(self) -> None:
        if self._running or self._done or self._closing:
            return
        self._running = True
        self.message_label.configure(text="")
        self.retry_button.grid_remove()
        self.continue_button.grid_remove()
        self.exit_button.grid()
        self.tools.reset_cancel()
        for key, bar in self._bars.items():
            bar.stop()
            self._started[key] = False
            bar.set(0)
            self._details[key].configure(text="En espera", text_color=theme.TEXT_MUTED)

        worker = threading.Thread(target=self._worker, name="bajape-setup", daemon=True)
        worker.start()

    def _worker(self) -> None:
        try:
            status = self.tools.ensure(
                lambda key, fraction, message: self._messages.put(
                    ("progress", key, fraction, message)
                )
            )
            self._messages.put(("done", status))
        except ToolCancelled:
            self._messages.put(("cancelled", None))
        except Exception as error:  # noqa: BLE001
            self._messages.put(("error", str(error)))

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            while True:
                message = self._messages.get_nowait()
                kind = message[0]
                if kind == "progress":
                    self._on_progress(message[1], message[2], message[3])
                elif kind == "done":
                    self._on_done(message[1])
                elif kind == "error":
                    self._on_error(message[1])
                elif kind == "cancelled" and not self._closing:
                    self._on_error("La preparación fue cancelada.")
        except queue.Empty:
            pass
        try:
            self.after(90, self._poll)
        except tk.TclError:
            pass

    def _on_progress(self, key: str, fraction: float, message: str) -> None:
        bar = self._bars.get(key)
        detail = self._details.get(key)
        if bar is None or detail is None:
            return
        color = theme.TEXT_MUTED
        lowered = message.lower()
        if "sin runtime" in lowered or "no se pudo" in lowered:
            color = theme.WARNING
        elif "instalado" in lowered or "listo" in lowered or "detectado" in lowered:
            color = theme.SUCCESS
        detail.configure(text=message, text_color=color)
        if fraction < 0:
            if not self._started[key]:
                bar.start()
                self._started[key] = True
        else:
            if self._started[key]:
                bar.stop()
                self._started[key] = False
            bar.set(max(0.0, min(1.0, fraction)))

    def _on_done(self, status: ToolStatus) -> None:
        self._running = False
        self._done = True
        self._status = status
        for key, bar in self._bars.items():
            if self._started[key]:
                bar.stop()
                self._started[key] = False
            bar.set(1.0)
            if key == "ytdlp":
                self._details[key].configure(text="Listo", text_color=theme.SUCCESS)
            elif key == "ffmpeg":
                self._details[key].configure(text="Listo", text_color=theme.SUCCESS)
        if not status.has_js_runtime:
            self._details["runtime"].configure(
                text="Sin runtime JS: algunas calidades pueden faltar",
                text_color=theme.WARNING,
            )
            self.message_label.configure(
                text=(
                    "Aviso: no hay runtime de JavaScript. YouTube puede limitar "
                    "algunas calidades. Puedes instalar Node.js o Deno y reintentar."
                ),
                text_color=theme.WARNING,
            )
        else:
            self._details["runtime"].configure(
                text="Listo", text_color=theme.SUCCESS
            )
            self.message_label.configure(
                text="Todo listo. Ya puedes descargar video y música.",
                text_color=theme.SUCCESS,
            )
        self.exit_button.grid_remove()
        self.retry_button.grid_remove()
        self.continue_button.grid()
        self.continue_button.focus_set()

    def _on_error(self, message: str) -> None:
        self._running = False
        self.message_label.configure(text=message, text_color=theme.ERROR)
        self.retry_button.grid()
        self.continue_button.grid_remove()

    def _retry(self) -> None:
        self._done = False
        self._start()

    def _continue(self) -> None:
        status = self._status
        self._closing = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if status is not None:
            self.on_ready(status)

    def _abort(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.tools.cancel()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_exit()


class UpdateDialog(ctk.CTkToplevel):
    """Actualiza yt-dlp sin salir de la aplicación."""

    def __init__(
        self,
        master: tk.Misc,
        tools: ToolManager,
        on_done: Callable[[str], None],
    ) -> None:
        super().__init__(master)
        self.tools = tools
        self.on_done = on_done
        self._messages: queue.Queue[tuple] = queue.Queue()
        self._running = False
        self._closing = False
        self._indeterminate = False
        self._version = ""

        self.title("BajaPe - Actualizar yt-dlp")
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)

        self.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 6))
        ctk.CTkLabel(
            header,
            text="Actualizar yt-dlp",
            font=(theme.FONT, 20, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text=(
                "YouTube cambia a menudo. Actualizar el motor suele resolver "
                "errores de descarga y calidades que faltan."
            ),
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=470,
        ).pack(anchor="w", pady=(4, 0))

        card = ctk.CTkFrame(
            self,
            fg_color=theme.SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=theme.BORDER,
        )
        card.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)
        self.detail_label = ctk.CTkLabel(
            card,
            text="Preparando...",
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        )
        self.detail_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self.progress = ctk.CTkProgressBar(
            card,
            height=8,
            corner_radius=4,
            fg_color=theme.SURFACE_HOVER,
            progress_color=theme.ACCENT,
        )
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", padx=14, pady=(8, 14))

        self.message_label = ctk.CTkLabel(
            self,
            text="",
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=500,
        )
        self.message_label.grid(row=2, column=0, sticky="ew", padx=24, pady=(10, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=24, pady=(14, 20))
        footer.grid_columnconfigure(0, weight=1)
        self.retry_button = ctk.CTkButton(
            footer,
            text="Reintentar",
            width=110,
            height=34,
            fg_color=theme.SURFACE_HOVER,
            hover_color=theme.BORDER,
            text_color=theme.TEXT,
            font=(theme.FONT, 12),
            command=self._start,
        )
        self.retry_button.grid(row=0, column=1, padx=(0, 8))
        self.retry_button.grid_remove()
        self.close_button = ctk.CTkButton(
            footer,
            text="Cerrar",
            width=100,
            height=34,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color="#FFFFFF",
            font=(theme.FONT, 12, "bold"),
            command=self._close,
        )
        self.close_button.grid(row=0, column=2)

        center_on_parent(self, master, 560, 330)
        make_modal(self)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._start)
        self.after(80, self._poll)

    def _start(self) -> None:
        if self._running or self._closing:
            return
        self._running = True
        self._version = ""
        self.retry_button.grid_remove()
        self.message_label.configure(text="")
        self.detail_label.configure(text="Descargando última versión...", text_color=theme.TEXT_MUTED)
        self.progress.stop()
        self._indeterminate = False
        self.progress.set(0)
        self.tools.reset_cancel()
        threading.Thread(target=self._worker, name="bajape-update", daemon=True).start()

    def _worker(self) -> None:
        try:
            version = self.tools.update_ytdlp(
                lambda key, fraction, message: self._messages.put(
                    ("progress", (fraction, message))
                )
            )
            self._messages.put(("done", version))
        except ToolCancelled:
            self._messages.put(("cancelled", None))
        except Exception as error:  # noqa: BLE001
            self._messages.put(("error", str(error)))

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, value = self._messages.get_nowait()
                if kind == "progress":
                    fraction, message = value
                    self._on_progress(fraction, message)
                elif kind == "done":
                    self._on_done(value)
                elif kind == "error":
                    self._on_error(value)
        except queue.Empty:
            pass
        try:
            self.after(90, self._poll)
        except tk.TclError:
            pass

    def _on_progress(self, fraction: float, message: str) -> None:
        self.detail_label.configure(text=message)
        if fraction < 0:
            if not self._indeterminate:
                self.progress.start()
                self._indeterminate = True
        else:
            if self._indeterminate:
                self.progress.stop()
                self._indeterminate = False
            self.progress.set(max(0.0, min(1.0, fraction)))

    def _on_done(self, version: str) -> None:
        self._running = False
        self._version = version
        if self._indeterminate:
            self.progress.stop()
            self._indeterminate = False
        self.progress.set(1.0)
        self.detail_label.configure(text=f"yt-dlp {version}", text_color=theme.SUCCESS)
        self.message_label.configure(
            text="Actualización completada.", text_color=theme.SUCCESS
        )
        self.close_button.configure(text="Cerrar")
        self.on_done(version)

    def _on_error(self, message: str) -> None:
        self._running = False
        if self._indeterminate:
            self.progress.stop()
            self._indeterminate = False
        self.detail_label.configure(text="No se pudo actualizar", text_color=theme.ERROR)
        self.message_label.configure(text=message, text_color=theme.ERROR)
        self.retry_button.grid()

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._running:
            self.tools.cancel()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class SettingsDialog(ctk.CTkToplevel):
    """Ajustes generales de descargas."""

    def __init__(
        self,
        master: tk.Misc,
        settings: SettingsStore,
        manager: DownloadManager,
        on_save: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.settings = settings
        self.manager = manager
        self.on_save = on_save

        self.title("BajaPe - Ajustes")
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 10))
        ctk.CTkLabel(
            header,
            text="Ajustes",
            font=(theme.FONT, 20, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Opciones que se aplican a las próximas descargas.",
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        card = ctk.CTkFrame(
            self,
            fg_color=theme.SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=theme.BORDER,
        )
        card.grid(row=1, column=0, sticky="ew", padx=24)
        card.grid_columnconfigure(1, weight=1)

        row = 0
        ctk.CTkLabel(
            card,
            text="Descargas simultáneas",
            font=(theme.FONT, 12),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(14, 10), pady=(14, 6))
        self.concurrency_var = tk.StringVar(value=str(settings.get("max_concurrent", 3)))
        ctk.CTkOptionMenu(
            card,
            values=["1", "2", "3", "4"],
            variable=self.concurrency_var,
            width=90,
            height=30,
            fg_color=theme.SURFACE_ALT,
            button_color=theme.SURFACE_HOVER,
            button_hover_color=theme.BORDER,
            dropdown_fg_color=theme.SURFACE_ALT,
            dropdown_hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11),
        ).grid(row=row, column=1, sticky="e", padx=(0, 14), pady=(14, 6))

        row += 1
        ctk.CTkLabel(
            card,
            text="Cookies del navegador",
            font=(theme.FONT, 12),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(14, 10), pady=6)
        cookie_labels = [label for _, label in COOKIE_BROWSER_CHOICES]
        self._cookie_by_label = {label: value for value, label in COOKIE_BROWSER_CHOICES}
        current_cookie = settings.get("cookies_browser", "")
        current_label = next(
            (label for value, label in COOKIE_BROWSER_CHOICES if value == current_cookie),
            cookie_labels[0],
        )
        self.cookies_var = tk.StringVar(value=current_label)
        ctk.CTkOptionMenu(
            card,
            values=cookie_labels,
            variable=self.cookies_var,
            width=130,
            height=30,
            fg_color=theme.SURFACE_ALT,
            button_color=theme.SURFACE_HOVER,
            button_hover_color=theme.BORDER,
            dropdown_fg_color=theme.SURFACE_ALT,
            dropdown_hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11),
        ).grid(row=row, column=1, sticky="e", padx=(0, 14), pady=6)

        row += 1
        ctk.CTkLabel(
            card,
            text="Abrir carpeta al terminar",
            font=(theme.FONT, 12),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(14, 10), pady=(6, 14))
        self.open_var = tk.BooleanVar(value=bool(settings.get("open_folder_when_done", False)))
        ctk.CTkSwitch(
            card,
            text="",
            variable=self.open_var,
            onvalue=True,
            offvalue=False,
            width=44,
            progress_color=theme.ACCENT,
            button_color=theme.TEXT,
            button_hover_color=theme.TEXT,
        ).grid(row=row, column=1, sticky="e", padx=(0, 14), pady=(6, 14))

        note = ctk.CTkLabel(
            self,
            text=(
                "Las cookies permiten descargar videos con restricción de edad o "
                "privados. Si usas Chrome o Edge, cierralos si la descarga da error "
                "de cookies."
            ),
            font=(theme.FONT, 10),
            text_color=theme.TEXT_FAINT,
            anchor="w",
            justify="left",
            wraplength=500,
        )
        note.grid(row=2, column=0, sticky="ew", padx=24, pady=(10, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=24, pady=(18, 20))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer,
            text="Cancelar",
            width=100,
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color=theme.BORDER,
            hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT_MUTED,
            font=(theme.FONT, 12),
            command=self._cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer,
            text="Guardar",
            width=110,
            height=34,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color="#FFFFFF",
            font=(theme.FONT, 12, "bold"),
            command=self._save,
        ).grid(row=0, column=2)

        center_on_parent(self, master, 540, 420)
        make_modal(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _save(self) -> None:
        try:
            concurrency = int(self.concurrency_var.get())
        except ValueError:
            concurrency = 3
        cookies = self._cookie_by_label.get(self.cookies_var.get(), "")
        self.settings.update(
            {
                "max_concurrent": concurrency,
                "cookies_browser": cookies,
                "open_folder_when_done": bool(self.open_var.get()),
            }
        )
        self.manager.set_max_concurrent(concurrency)
        self.manager.open_when_done = bool(self.open_var.get())
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_save()

    def _cancel(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
