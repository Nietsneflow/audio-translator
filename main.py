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


if __name__ == "__main__":
    _check_python_version()
    _check_dependencies()
    setup_logging()   # initialise before any module uses get_logger()

    import logging
    log = logging.getLogger(__name__)
    log.info("=== Application starting ===")

    # Import GUI only after deps are confirmed (avoids partial-import errors)
    from gui import App

    app = App()
    app.mainloop()
    log.info("=== Application exited ===")
