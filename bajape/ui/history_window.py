"""Ventana de historial de descargas."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk

from .. import paths
from ..storage import HistoryStore
from ..util import format_datetime, truncate
from . import theme
from .dialogs import center_on_parent


class HistoryWindow(ctk.CTkToplevel):
    MAX_RENDER = 400
    SEARCH_PLACEHOLDER = "Buscar por título o ruta..."

    def __init__(
        self,
        master: tk.Misc,
        history: HistoryStore,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.history = history
        self.on_close = on_close
        self._closed = False

        self.title("BajaPe - Historial")
        self.configure(fg_color=theme.BG)
        self.minsize(760, 480)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 10))
        header.grid_columnconfigure(1, weight=1)
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_box,
            text="Historial",
            font=(theme.FONT, 22, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(anchor="w")
        self.count_label = ctk.CTkLabel(
            title_box,
            text="",
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        )
        self.count_label.pack(anchor="w")
        self.search_entry = ctk.CTkEntry(
            header,
            placeholder_text=self.SEARCH_PLACEHOLDER,
            width=280,
            height=34,
            corner_radius=9,
            fg_color=theme.SURFACE,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            placeholder_text_color=theme.TEXT_FAINT,
            font=(theme.FONT, 11),
        )
        self.search_entry.grid(row=0, column=1, sticky="e", padx=(20, 0))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)

        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.SURFACE_HOVER,
        )
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 6))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self.note_label = ctk.CTkLabel(
            self,
            text="",
            font=(theme.FONT, 10),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        )
        self.note_label.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 4))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=24, pady=(6, 18))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer,
            text="Vaciar historial",
            width=130,
            height=32,
            fg_color="transparent",
            hover_color=theme.ACCENT_SOFT,
            text_color=theme.ERROR,
            font=(theme.FONT, 11),
            command=self._clear,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer,
            text="Cerrar",
            width=100,
            height=32,
            fg_color=theme.SURFACE_HOVER,
            hover_color=theme.BORDER,
            text_color=theme.TEXT,
            font=(theme.FONT, 11),
            command=self._close,
        ).grid(row=0, column=2)

        center_on_parent(self, master, 920, 620)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(60, self._lift)
        self.refresh()

    def _lift(self) -> None:
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.destroy()
        self.on_close()

    def _on_search_change(self, event=None) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self._closed:
            return
        for child in self.list_frame.winfo_children():
            child.destroy()

        query = self.search_entry.get().strip().lower()
        entries = self.history.all()
        if query:
            entries = [
                entry
                for entry in entries
                if query in entry.get("title", "").lower()
                or query in entry.get("path", "").lower()
                or query in entry.get("url", "").lower()
            ]
        total = len(entries)
        shown = entries[: self.MAX_RENDER]
        self.count_label.configure(
            text=f"{total} descarga{'s' if total != 1 else ''} guardada{'s' if total != 1 else ''}"
        )
        self.note_label.configure(
            text=(
                f"Mostrando las {len(shown)} más recientes de {total}."
                if total > len(shown)
                else ""
            )
        )

        if not shown:
            message = (
                "No hay coincidencias con la búsqueda."
                if query
                else "Todavía no hay descargas en el historial."
            )
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent", height=180)
            empty.grid(row=0, column=0, sticky="nsew", pady=60)
            ctk.CTkLabel(
                empty,
                text="Aquí aparecerán tus descargas",
                font=(theme.FONT, 15, "bold"),
                text_color=theme.TEXT_MUTED,
            ).pack(pady=(30, 4))
            ctk.CTkLabel(
                empty,
                text=message,
                font=(theme.FONT, 11),
                text_color=theme.TEXT_FAINT,
            ).pack()
            return

        for index, entry in enumerate(shown):
            self._render_row(index, entry)

    def _render_row(self, index: int, entry: dict) -> None:
        path = entry.get("path", "")
        exists = bool(path) and Path(path).is_file()
        card = ctk.CTkFrame(
            self.list_frame,
            fg_color=theme.SURFACE,
            corner_radius=10,
            border_width=1,
            border_color=theme.BORDER,
        )
        card.grid(row=index, column=0, sticky="ew", pady=(0, 6), padx=4)
        card.grid_columnconfigure(1, weight=1)

        is_audio = entry.get("kind") == "audio"
        ctk.CTkLabel(
            card,
            text="MP3" if is_audio else "VIDEO",
            width=52,
            height=22,
            corner_radius=6,
            fg_color=theme.ACCENT_SOFT if is_audio else theme.SURFACE_ALT,
            text_color=theme.ACCENT if is_audio else theme.TEXT_MUTED,
            font=(theme.FONT, 9, "bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(12, 12), pady=12, sticky="n")

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", pady=(10, 10))
        info.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            info,
            text=truncate(entry.get("title", "Sin título"), 78),
            font=(theme.FONT, 12, "bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        path_text = truncate(path, 95) if path else "Ruta no disponible"
        if not exists:
            path_text += "  (archivo no encontrado)"
        ctk.CTkLabel(
            info,
            text=path_text,
            font=(theme.FONT, 10),
            text_color=theme.TEXT_FAINT if not exists else theme.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        meta = " · ".join(
            part
            for part in (
                format_datetime(entry.get("timestamp", "")),
                entry.get("quality", ""),
            )
            if part
        )
        ctk.CTkLabel(
            info,
            text=meta,
            font=(theme.FONT, 9),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(2, 0))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e", padx=(10, 12), pady=10)
        open_button = ctk.CTkButton(
            actions,
            text="Abrir",
            width=64,
            height=27,
            corner_radius=7,
            fg_color=theme.SURFACE_HOVER,
            hover_color=theme.BORDER,
            text_color=theme.TEXT,
            font=(theme.FONT, 10),
            command=lambda p=path: paths.open_path(p),
        )
        open_button.pack(side="left", padx=(0, 5))
        if not exists:
            open_button.configure(state="disabled", fg_color=theme.SURFACE_ALT)
        ctk.CTkButton(
            actions,
            text="Carpeta",
            width=70,
            height=27,
            corner_radius=7,
            fg_color=theme.SURFACE_ALT,
            hover_color=theme.SURFACE_HOVER,
            text_color=theme.TEXT_MUTED,
            font=(theme.FONT, 10),
            command=lambda p=path: paths.reveal_path(p) if p else None,
        ).pack(side="left")

    def _clear(self) -> None:
        if not self.history.all():
            return
        confirmed = messagebox.askyesno(
            "Vaciar historial",
            "¿Borrar todas las entradas del historial?\n\nLos archivos descargados no se eliminan.",
            parent=self,
        )
        if not confirmed:
            return
        self.history.clear()
        self.refresh()
