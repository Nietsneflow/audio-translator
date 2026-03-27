"""
transcriber.py
Drains the audio queue produced by AudioCaptureThread, runs
faster-whisper translation (Russian → English), and fires a
callback on the main thread with the result text.
"""

import os
import sys
import threading
import queue
import time
from typing import Callable

import numpy as np

from logger import get_logger

try:
    import psutil as _psutil  # type: ignore[import]
except ImportError:
    _psutil = None  # type: ignore



# Keep strong references to every AddedDllDirectory object returned by
# os.add_dll_directory().  Python will call __exit__ (which de-registers the
# directory) when the object is garbage-collected, so we must hold onto them
# for the lifetime of the process.
_DLL_DIR_HANDLES: list = []

# ctranslate2's GPU WhisperModel destructor throws a C++ exception when called
# from a background thread, which propagates through Python's noexcept C-extension
# boundary and triggers std::terminate() (Windows event 0xc0000409 in ucrtbase.dll).
# This is a ctranslate2 bug — destructors must never throw.
# Workaround: keep a strong reference to every retired model alive until process
# exit.  The OS reclaims all CUDA/RAM resources on process exit without running
# C++ destructors, so there is no crash and no leak — the VRAM/RAM is returned
# to the system the moment the process terminates.
_RETIRED_MODELS: list = []


def _register_cuda_dll_dirs() -> None:
    """
    Add the DLL directories bundled by the nvidia-* Python wheels to the
    Windows DLL search path so ctranslate2/cublas can load them at runtime.

    nvidia-cublas-cu12 and nvidia-cudnn-cu12 install their DLLs under:
      <site-packages>/nvidia/<package>/bin/
    Windows only searches PATH and the application directory by default, so
    we must explicitly register each bin/ folder with os.add_dll_directory().
    The returned handle MUST be kept alive (stored in _DLL_DIR_HANDLES) or
    Windows removes the directory from the search path when it is GC'd.
    """
    if sys.platform != "win32":
        return
    log = get_logger(__name__)

    def _add(bin_dir: str) -> None:
        if os.path.isdir(bin_dir):
            try:
                handle = os.add_dll_directory(bin_dir)
                _DLL_DIR_HANDLES.append(handle)  # prevent GC / de-registration
                log.debug("Registered CUDA DLL dir: %s", bin_dir)
            except OSError:
                pass

    # 1. nvidia-* wheels  (<site-packages>/nvidia/<pkg>/bin/)
    for path in sys.path:
        nvidia_root = os.path.join(path, "nvidia")
        if not os.path.isdir(nvidia_root):
            continue
        for pkg in os.listdir(nvidia_root):
            _add(os.path.join(nvidia_root, pkg, "bin"))

    # 2. PyTorch bundled CUDA DLLs  (<site-packages>/torch/lib/)
    try:
        import torch  # type: ignore[import]
        _add(os.path.join(os.path.dirname(torch.__file__), "lib"))
    except ImportError:
        pass


_register_cuda_dll_dirs()

log = get_logger(__name__)


# Maps UI labels to faster-whisper model size strings
MODEL_OPTIONS = {
    "small  (fast, ~500 MB)": "small",
    "medium (balanced, ~1.5 GB)": "medium",
    "large-v3 (best, ~3 GB)": "large-v3",
}
DEFAULT_MODEL_LABEL = "medium (balanced, ~1.5 GB)"


class TranscriberThread(threading.Thread):
    """
    Background thread that:
      1. Loads a faster-whisper WhisperModel (CUDA if available, else CPU).
      2. Reads (tag, payload) tuples from *audio_queue*:
           - ("audio", np.ndarray)  → transcribe/translate and call *on_result*
           - ("error", str)         → forward error to *on_error*
           - ("stop", None)         → exit cleanly
      3. Calls *on_result(timestamp: str, text: str)* for each translated segment.
      4. Calls *on_error(message: str)* on errors.
      5. Calls *on_status(message: str)* for status messages (model loading, etc.).
      6. Calls *on_device_info(device: str, compute_type: str)* once the model
         is loaded so the GUI can show a GPU/CPU indicator.
    """

    def __init__(
        self,
        audio_queue: queue.Queue,
        on_result: Callable[[str, str], None],
        on_error: Callable[[str], None],
        on_status: Callable[[str], None],
        on_device_info: Callable[[str, str], None] | None = None,
        model_label: str = DEFAULT_MODEL_LABEL,
        force_device: str = "Auto",  # "Auto" | "GPU" | "CPU"
    ):
        super().__init__(daemon=True, name="TranscriberThread")
        self.audio_queue = audio_queue
        self.on_result = on_result
        self.on_error = on_error
        self.on_status = on_status
        self.on_device_info = on_device_info or (lambda d, c: None)
        self.model_size = MODEL_OPTIONS.get(model_label, "medium")
        self.force_device = force_device
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()
        # Unblock the queue.get() call
        self.audio_queue.put(("stop", None))

    def run(self):
        log.info("TranscriberThread starting (model=%s)", self.model_size)
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            msg = "faster-whisper is not installed.\nRun: pip install faster-whisper"
            log.error(msg)
            self.on_error(msg)
            return

        # Determine compute device / type
        device, compute_type = self._select_device(self.force_device)
        log.info("Selected device=%s compute_type=%s (force=%s)", device, compute_type, self.force_device)
        self.on_status(f"Loading '{self.model_size}' model on {device.upper()} ({compute_type})…")

        model = None
        # If CUDA DLLs are missing at load time, fall back to CPU automatically.
        for attempt_device, attempt_ct in [(device, compute_type), ("cpu", "int8")]:
            try:
                # Large models on CPU require 4–6 GB RAM. Warn if it looks tight.
                if attempt_device == "cpu" and self.model_size in ("large", "large-v2", "large-v3"):
                    if _psutil is not None:
                        try:
                            free_gb = _psutil.virtual_memory().available / 1024 ** 3
                            if free_gb < 4.0:
                                self.on_status(
                                    f"WARNING: '{self.model_size}' on CPU needs ~4–6 GB free RAM "
                                    f"(only {free_gb:.1f} GB available). Loading may fail or be very slow."
                                )
                                log.warning(
                                    "Low RAM for large model on CPU: %.1f GB free", free_gb
                                )
                        except Exception:
                            pass

                model = WhisperModel(self.model_size, device=attempt_device, compute_type=attempt_ct)
                if attempt_device != device:
                    log.warning(
                        "CUDA load failed — fell back to CPU (int8). "
                        "Install nvidia-cublas-cu12 + nvidia-cudnn-cu12 for GPU acceleration."
                    )
                    self.on_status(
                        f"GPU unavailable — running on CPU. "
                        f"Install nvidia-cublas-cu12 & nvidia-cudnn-cu12 for faster performance."
                    )
                    device = attempt_device
                log.info("Model '%s' loaded on %s", self.model_size, attempt_device.upper())
                self.on_device_info(attempt_device, attempt_ct)
                break
            except MemoryError:
                log.error("Out of memory loading '%s' on %s", self.model_size, attempt_device)
                if attempt_device == "cpu":
                    self.on_error(
                        f"Not enough RAM to load the '{self.model_size}' model.\n"
                        f"Try a smaller model (medium or small) or free up memory."
                    )
                    return
            except Exception as exc:  # noqa: BLE001
                err_str = str(exc)
                # ctranslate2 OOM surfaces as a RuntimeError with "out of memory" text
                if "out of memory" in err_str.lower() or "alloc" in err_str.lower():
                    log.error("OOM loading '%s' on %s: %s", self.model_size, attempt_device, exc)
                    if attempt_device == "cpu":
                        self.on_error(
                            f"Not enough memory to load the '{self.model_size}' model.\n"
                            f"Try a smaller model (medium or small)."
                        )
                        return
                else:
                    log.warning("Model load failed on %s: %s", attempt_device, exc)
                    if attempt_device == "cpu":
                        log.exception("CPU fallback also failed")
                        self.on_error(f"Failed to load Whisper model: {exc}")
                        return

        if model is None:
            self.on_error("Could not load Whisper model on any device.")
            return

        self.on_status("Ready — listening…")

        # Utterances are processed in strict FIFO order from the queue.
        # A single transcriber thread guarantees translations appear in the
        # same order they were captured, regardless of batch size.
        # When a backlog builds up, multiple queued utterances are merged into
        # one Whisper call — reducing overhead and speeding up catch-up.
        utterance_seq = 0
        SAMPLE_RATE = 16_000
        MAX_BATCH_SECONDS = 60   # hard cap per inference call
        # 0.5 s of silence inserted between joined utterances so Whisper
        # sees sentence boundaries rather than run-on audio.
        SILENCE_GAP = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)

        while not self._stop_event.is_set():
            try:
                tag, payload = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if tag == "stop":
                break
            if tag == "error":
                self.on_error(payload)
                continue
            if tag == "status":
                self.on_status(payload)
                continue
            if tag != "audio":
                continue

            # ── batch drain ───────────────────────────────────────────────
            # We have the first audio chunk. Non-blocking drain any
            # additional audio items already waiting in the queue so we
            # can process them all in one Whisper call.
            audio_parts = [payload]
            seq_start = utterance_seq + 1
            total_samples = len(payload)

            while total_samples < MAX_BATCH_SECONDS * SAMPLE_RATE:
                try:
                    next_tag, next_payload = self.audio_queue.get_nowait()
                except queue.Empty:
                    break  # nothing more waiting right now

                if next_tag == "stop":
                    self._stop_event.set()
                    break
                if next_tag == "error":
                    self.on_error(next_payload)
                    break
                if next_tag == "audio":
                    audio_parts.append(next_payload)
                    total_samples += len(next_payload)
                # status / unknown tags are silently consumed here — they
                # are stale by the time the batch finishes anyway

            utterance_seq += len(audio_parts)
            seq_end = utterance_seq
            seq_label = f"#{seq_start}" if seq_start == seq_end else f"#{seq_start}–{seq_end}"
            batch_count = len(audio_parts)

            # Join parts with short silence gaps between them
            if batch_count == 1:
                audio_data = audio_parts[0]
            else:
                interleaved = []
                for i, part in enumerate(audio_parts):
                    interleaved.append(part)
                    if i < len(audio_parts) - 1:
                        interleaved.append(SILENCE_GAP)
                audio_data = np.concatenate(interleaved)

            duration = len(audio_data) / SAMPLE_RATE
            try:
                if batch_count > 1:
                    log.info(
                        "Batch transcribe %s (%d utterances, %.2f s total)",
                        seq_label, batch_count, duration
                    )
                    self.on_status(
                        f"Translating {seq_label} "
                        f"({batch_count} utterances batched, {duration:.1f}s)…"
                    )
                else:
                    log.debug("Transcribing utterance %s (%.2f s)", seq_label, duration)
                    self.on_status(f"Translating {seq_label} ({duration:.1f}s)…")

                segments, info = model.transcribe(
                    audio_data,
                    language="ru",
                    task="translate",   # outputs English directly
                    beam_size=5,
                    vad_filter=True,    # re-enable to skip the silence gaps we inserted
                    vad_parameters={"min_silence_duration_ms": 300},
                )
                log.debug(
                    "%s — language=%s (prob=%.2f)",
                    seq_label, info.language, info.language_probability
                )
                for segment in segments:
                    text = segment.text.strip()
                    if text:
                        ts = time.strftime("%H:%M:%S")
                        log.info("[%s] %s %s", ts, seq_label, text)
                        self.on_result(ts, text)
                # Never break out of the segment generator mid-inference.
                # Interrupting the lazy ctranslate2 generator leaves its
                # internal C++ decoder state allocated; when the model is
                # later GC'd the destructor crashes with std::terminate()
                # (exception code 0xc0000409 in ucrtbase.dll).  Instead we
                # let the current utterance finish, then the outer while
                # loop's stop-event check exits cleanly.
                self.on_status("Listening…")
            except Exception as exc:  # noqa: BLE001
                # Detect missing CUDA DLL errors and retry on CPU rather than
                # crashing the whole app.
                dll_error = "dll" in str(exc).lower() or "library" in str(exc).lower()
                if dll_error and device == "cuda":
                    log.exception("CUDA DLL error on %s — retrying on CPU", seq_label)
                    self.on_status("CUDA DLL error — reloading model on CPU…")
                    try:
                        model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
                        device = "cpu"
                        log.warning("Permanently switched to CPU (int8) after CUDA DLL failure")
                        self.on_device_info("cpu", "int8")
                        self.on_status("Switched to CPU. Install nvidia-cublas-cu12 for GPU.")
                        # Re-queue this batch so it gets translated
                        self.audio_queue.put(("audio", audio_data))
                    except Exception as cpu_exc:  # noqa: BLE001
                        log.exception("CPU reload also failed")
                        self.on_error(f"Fatal transcription error: {cpu_exc}")
                else:
                    log.exception("Transcription error on %s", seq_label)
                    self.on_error(f"Transcription error: {exc}")

        # Park the model in _RETIRED_MODELS so Python's GC never runs
        # ctranslate2's crashing C++ destructor.  See module-level comment.
        try:
            _RETIRED_MODELS.append(model)
            log.debug("Model parked in _RETIRED_MODELS (destructor suppressed)")
        except Exception:
            pass

        log.info("TranscriberThread stopped")
        self.on_status("Stopped.")

    @staticmethod
    def _select_device(force: str = "Auto") -> tuple[str, str]:
        """Return (device, compute_type) based on force setting or auto-detect."""
        if force == "CPU":
            log.info("Device forced to CPU (int8) by user")
            return "cpu", "int8"
        if force == "GPU":
            log.info("Device forced to CUDA (float16) by user")
            return "cuda", "float16"
        # Auto: prefer CUDA, fall back to CPU
        try:
            import torch  # type: ignore[import]
            if torch.cuda.is_available():
                log.info("CUDA available via torch — using GPU")
                return "cuda", "float16"
        except ImportError:
            pass
        # Try CTranslate2 CUDA directly (faster-whisper can use it without torch).
        # get_supported_compute_types("cuda") returns a set of precision strings
        # (e.g. {'float16', 'int8', ...}) — a non-empty set means CUDA is usable.
        try:
            import ctranslate2
            supported = ctranslate2.get_supported_compute_types("cuda")
            if supported:
                compute_type = "float16" if "float16" in supported else "int8"
                log.info(
                    "CUDA available via ctranslate2 (compute_type=%s, all supported: %s)",
                    compute_type, supported
                )
                return "cuda", compute_type
        except Exception as exc:  # noqa: BLE001
            log.warning("ctranslate2 CUDA probe failed: %s", exc)
        log.warning("CUDA not available — falling back to CPU (int8)")
        return "cpu", "int8"
