"""Punto de entrada de BajaPe."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from bajape import APP_NAME, __version__, paths


def _setup_logging() -> None:
    handler = RotatingFileHandler(
        paths.log_path(),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _install_exception_hook() -> None:
    logger = logging.getLogger("bajape")

    def handle(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Error no controlado",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        try:
            from tkinter import messagebox

            messagebox.showerror(
                APP_NAME,
                "Ocurrió un error inesperado. Se guardó el detalle en:\n\n"
                f"{paths.log_path()}",
            )
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = handle


def main() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

    _setup_logging()
    _install_exception_hook()
    logging.getLogger("bajape").info("Iniciando %s %s", APP_NAME, __version__)

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(paths.APP_ID)
    except Exception:  # noqa: BLE001
        pass

    from bajape.ui.main_window import BajaPeApp

    app = BajaPeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
