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
from importlib.util import find_spec

from logger import setup_logging


def _fatal(message: str):
    """Exit with a visible error.  The app is normally launched with pythonw
    (no console), where sys.exit's stderr message is invisible — show a
    Windows message box so a missing-dependency failure is never silent."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Audio Translate", 0x10)  # MB_ICONERROR
    except Exception:
        pass
    sys.exit(message)


def _check_python_version():
    if sys.version_info < (3, 10):
        _fatal(
            f"Python 3.10 or newer is required (you have {sys.version}).\n"
            "Download from https://www.python.org/downloads/"
        )


def _check_dependencies():
    # find_spec checks presence without importing — importing faster_whisper
    # here would pull in ctranslate2 and add seconds before any window shows.
    missing = [pkg for pkg in ("soundcard", "faster_whisper", "numpy")
               if find_spec(pkg) is None]
    if missing:
        _fatal(
            "Missing dependencies: " + ", ".join(missing) + "\n"
            "Install them with:  pip install -r requirements.txt"
        )
    if find_spec("tkinter") is None or find_spec("_tkinter") is None:
        _fatal(
            "This Python installation is missing tkinter (tcl/tk).\n"
            "Reinstall Python with the 'tcl/tk and IDLE' option enabled."
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

    # Lower the whole process to below-normal priority so the OS and GPU
    # scheduler always favour foreground apps (games, video) over translation.
    # BELOW_NORMAL_PRIORITY_CLASS = 0x4000
    try:
        import ctypes
        # use_last_error so ctypes captures the error code at call time —
        # windll.kernel32.GetLastError() afterwards can report a stale value.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ok = kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x4000)
        if ok:
            log.info("Process priority set to BELOW_NORMAL")
        else:
            log.warning("SetPriorityClass failed (error %d)", ctypes.get_last_error())
    except Exception:
        pass

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
