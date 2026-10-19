"""Tarjeta visual para cada descarga de la cola."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from ..downloader import DownloadTask
from ..util import truncate
from . import theme


class QueueCard(ctk.CTkFrame):
    def __init__(
        self,
        master,
        task: DownloadTask,
        on_cancel: Callable[[str], None],
        on_retry: Callable[[str], None],
        on_remove: Callable[[str], None],
        on_open: Callable[[str], None],
    ) -> None:
        super().__init__(
            master,
            fg_color=theme.SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=theme.BORDER,
        )
        self.task_id = task.id
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._on_remove = on_remove
        self._on_open = on_open

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        self.title_label = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            font=(theme.FONT, 13, "bold"),
            text_color=theme.TEXT,
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=(12, 0))

        self.status_chip = ctk.CTkLabel(
            self,
            text="",
            height=22,
            corner_radius=11,
            padx=9,
            font=(theme.FONT, 10, "bold"),
        )
        self.status_chip.grid(row=0, column=1, sticky="e", padx=(0, 14), pady=(12, 0))

        self.progress = ctk.CTkProgressBar(
            self,
            height=8,
            corner_radius=4,
            fg_color=theme.SURFACE_HOVER,
            progress_color=theme.ACCENT,
        )
        self.progress.set(0)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 0))

        self.detail_label = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            wraplength=620,
            font=(theme.FONT, 11),
            text_color=theme.TEXT_MUTED,
        )
        self.detail_label.grid(row=2, column=0, sticky="ew", padx=(14, 8), pady=(6, 12))

        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.grid(row=2, column=1, sticky="e", padx=(0, 10), pady=(6, 12))
        for column in range(4):
            self.actions.grid_columnconfigure(column, weight=0)

        self.cancel_button = self._button("Cancelar", self._cancel, theme.ACCENT_SOFT, theme.TEXT)
        self.cancel_button.grid(row=0, column=0, padx=(0, 4))
        self.retry_button = self._button(
            "Reintentar", self._retry, theme.SURFACE_HOVER, theme.TEXT
        )
        self.retry_button.grid(row=0, column=1, padx=(0, 4))
        self.open_button = self._button("Abrir", self._open, theme.SURFACE_HOVER, theme.TEXT)
        self.open_button.grid(row=0, column=2, padx=(0, 4))
        self.remove_button = self._button(
            "Quitar", self._remove, theme.SURFACE_ALT, theme.TEXT_MUTED
        )
        self.remove_button.grid(row=0, column=3)

        self.update_task(task)

    def _button(
        self, text: str, command: Callable[[], None], fg_color: str, text_color: str
    ) -> ctk.CTkButton:
        return ctk.CTkButton(
            self.actions,
            text=text,
            width=68,
            height=26,
            corner_radius=7,
            font=(theme.FONT, 11),
            fg_color=fg_color,
            hover_color=theme.SURFACE_HOVER if fg_color != theme.ACCENT_SOFT else "#4A1D20",
            text_color=text_color,
            command=command,
        )

    # ----------------------------------------------------------------- acciones
    def _cancel(self) -> None:
        self._on_cancel(self.task_id)

    def _retry(self) -> None:
        self._on_retry(self.task_id)

    def _remove(self) -> None:
        self._on_remove(self.task_id)

    def _open(self) -> None:
        self._on_open(self.task_id)

    # ------------------------------------------------------------------ pintado
    def update_task(self, task: DownloadTask) -> None:
        self.title_label.configure(text=truncate(task.display_title, 74))

        label, background, foreground = theme.STATUS_META.get(
            task.status, ("Desconocido", theme.SURFACE_HOVER, theme.TEXT_MUTED)
        )
        self.status_chip.configure(text=label, fg_color=background, text_color=foreground)

        value = max(0.0, min(1.0, task.progress / 100.0))
        self.progress.set(value)
        self.progress.configure(
            progress_color=theme.PROGRESS_COLORS.get(task.status, theme.ACCENT)
        )

        detail = task.detail_text()
        if task.status in ("error", "partial") and task.error:
            detail = truncate(task.error, 220)
        elif task.status in ("downloading", "processing") and task.playlist_count > 1:
            detail = truncate(detail, 150)
        self.detail_label.configure(
            text=detail,
            text_color=theme.ERROR if task.status == "error" else theme.TEXT_MUTED,
        )

        active = task.is_active and task.status != "cancelling"
        queued = task.status == "queued"
        self._set_visible(self.cancel_button, active or queued)
        self._set_visible(
            self.retry_button, task.status in ("error", "cancelled", "partial")
        )
        self._set_visible(
            self.open_button,
            bool(task.filepath) and task.status in ("done", "partial", "error"),
        )
        self._set_visible(self.remove_button, task.is_terminal and task.status != "cancelling")

    @staticmethod
    def _set_visible(widget: ctk.CTkBaseClass, visible: bool) -> None:
        if visible:
            widget.grid()
        else:
            widget.grid_remove()
