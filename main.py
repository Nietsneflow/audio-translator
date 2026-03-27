"""
main.py
Entry point for the Russian → English Live Translator.

Usage:
    python main.py

Prerequisites (one-time):
    pip install -r requirements.txt

    For NVIDIA GPU acceleration also install the matching cuDNN wheel:
    pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
    (or ensure CUDA 11/12 + cuDNN 8/9 are in your PATH)
"""

import sys
import threading
import traceback

from logger import setup_logging


def _check_python_version():
    if sys.version_info < (3, 10):
        sys.exit(
            f"Python 3.10 or newer is required (you have {sys.version}).\n"
            "Download from https://www.python.org/downloads/"
        )


def _check_dependencies():
    missing = []
    for pkg in ("soundcard", "faster_whisper", "numpy"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        sys.exit(
            "Missing dependencies: " + ", ".join(missing) + "\n"
            "Install them with:  pip install -r requirements.txt"
        )


def _install_exception_hooks(log):
    """Route all unhandled exceptions (main thread, daemon threads, tkinter
    callbacks) into errors.log so a silent crash always leaves a trace."""

    def _log_exc(exc_type, exc_value, exc_tb):
        log.critical(
            "Unhandled exception in main thread",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    def _log_thread_exc(args):
        if args.exc_type is SystemExit:
            return  # normal exit, don't log
        log.critical(
            "Unhandled exception in thread %r",
            args.thread.name if args.thread else "<unknown>",
            exc_info=(args.exc_type, args.exc_value, args.exc_tb),
        )

    sys.excepthook = _log_exc
    threading.excepthook = _log_thread_exc


if __name__ == "__main__":
    _check_python_version()
    _check_dependencies()
    setup_logging()   # initialise before any module uses get_logger()

    import logging
    log = logging.getLogger(__name__)
    _install_exception_hooks(log)
    log.info("=== Application starting ===")

    # Import GUI only after deps are confirmed (avoids partial-import errors)
    from gui import App

    try:
        app = App()

        # Catch exceptions raised inside tkinter event callbacks (e.g. after(),
        # button commands, StringVar traces).  Without this they are silently
        # swallowed by the Tcl/Tk event loop when running under pythonw.
        def _tk_callback_exception(exc_type, exc_value, exc_tb):
            log.critical(
                "Unhandled exception in tkinter callback",
                exc_info=(exc_type, exc_value, exc_tb),
            )
            # Show a minimal error dialog so the user knows something went wrong
            try:
                import tkinter.messagebox as mb
                tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                mb.showerror(
                    "Unexpected Error",
                    f"The application encountered an unexpected error and may be "
                    f"unstable.\n\nDetails saved to logs\\errors.log:\n\n{tb_text[-800:]}",
                )
            except Exception:
                pass

        app.report_callback_exception = _tk_callback_exception
        app.mainloop()
    except Exception:
        log.critical("Fatal error in mainloop", exc_info=True)
        raise
    log.info("=== Application exited ===")
