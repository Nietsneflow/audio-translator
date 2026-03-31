"""
transcriber.py
Drains the audio queue produced by AudioCaptureThread, runs
faster-whisper translation (Russian → English), and fires a
callback on the main thread with the result text.
"""

import ctypes
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

# Local models directory — bundled alongside the script so no internet download
# is needed.  Structure: models/<size>/model.bin + config.json + tokenizer.json
# + vocabulary.txt.  If the folder for a given size is missing, faster-whisper
# falls back to downloading from HuggingFace automatically.
_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# ── Hallucination filters ─────────────────────────────────────────────────────

# Common single-word / short-phrase Whisper hallucinations on near-silence.
# All comparisons are done lower-case with punctuation stripped.
_PHANTOM_PHRASES: frozenset[str] = frozenset({
    "bye", "bye-bye", "bye bye", "goodbye", "good bye",
    "thanks", "thank you", "thank you.", "thanks.",
    "thanks for watching", "thank you for watching",
    "you", ".", "!", "...", "hmm", "hm", "um", "uh",
    "i", "oh", "ah", "okay", "ok", "yes", "no",
    "subscribe", "like and subscribe",
})


def _strip_punct(text: str) -> str:
    """Lowercase and strip leading/trailing punctuation for comparison."""
    return text.lower().strip(".,!?;:…- \t\n")


def _is_phantom(text: str, no_speech_prob: float, avg_logprob: float) -> bool:
    """Return True if the segment looks like a hallucination on near-silence."""
    stripped = _strip_punct(text)
    # Known phantom phrase list — language-model favourite fillers on silence
    if stripped in _PHANTOM_PHRASES:
        log.debug("Suppressed phantom phrase: %r", text)
        return True
    # Very short output with weak confidence is almost certainly noise
    word_count = len(stripped.split())
    if word_count <= 2 and avg_logprob < -0.8:
        log.debug("Suppressed low-confidence short segment (logprob=%.2f): %r", avg_logprob, text)
        return True
    # Segment-level no-speech probability above 0.7 even if global threshold passed
    if no_speech_prob > 0.7:
        log.debug("Suppressed high no_speech_prob=%.2f segment: %r", no_speech_prob, text)
        return True
    return False


def _is_repetition_loop(text: str, min_repeats: int = 6) -> bool:
    """Return True if *text* is a Whisper repetition loop.

    Checks n-grams of size 1–4.  A phrase repeated *min_repeats* or more times
    accounts for the vast majority of the segment — a clear hallucination loop.
    """
    words = text.split()
    total = len(words)
    if total < min_repeats * 2:
        return False
    for n in range(1, 5):
        if total < n:
            break
        ngrams = [tuple(words[i: i + n]) for i in range(total - n + 1)]
        if not ngrams:
            continue
        most_common = max(set(ngrams), key=ngrams.count)
        count = ngrams.count(most_common)
        # If the most-common n-gram makes up >60% of all n-gram slots, it's a loop
        if count >= min_repeats and count / len(ngrams) > 0.60:
            log.debug(
                "Suppressed repetition loop (n=%d, count=%d, phrase=%r): %r",
                n, count, " ".join(most_common), text[:120],
            )
            return True
    return False


def _resolve_model_path(model_size: str) -> str:
    """Return local path if bundled models exist, else the HuggingFace model ID."""
    local = os.path.join(_MODELS_DIR, model_size)
    if os.path.isfile(os.path.join(local, "model.bin")):
        log.debug("Using bundled local model: %s", local)
        return local
    log.debug("Local model not found for '%s', will download from HuggingFace", model_size)
    return model_size


# Maps UI labels to faster-whisper model size strings
MODEL_OPTIONS = {
    "small  (fast, ~500 MB)": "small",
    "medium (balanced, ~1.5 GB)": "medium",
}
DEFAULT_MODEL_LABEL = "medium (balanced, ~1.5 GB)"


class TranscriberThread(threading.Thread):
    """
    Background thread that:
      1. Loads a faster-whisper WhisperModel (CUDA if available, else CPU).
      2. Reads (tag, payload, source_id) 3-tuples from *audio_queue*:
           - ("audio", np.ndarray, int)  → transcribe/translate and call *on_result*
           - ("error", str, int)         → forward error to *on_error*
           - ("stop", None, 0)           → exit cleanly
      3. Calls *on_result(timestamp, text, language, source_id)* for each segment.
      4. Calls *on_error(message: str)* on errors.
      5. Calls *on_status(message: str)* for status messages (model loading, etc.).
      6. Calls *on_device_info(device: str, compute_type: str)* once the model
         is loaded so the GUI can show a GPU/CPU indicator.
    """

    def __init__(
        self,
        audio_queue: queue.Queue,
        on_result: Callable[[str, str, str, int], None],
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
        self.audio_queue.put(("stop", None, 0))

    def run(self):
        log.info("TranscriberThread starting (model=%s)", self.model_size)
        # Lower this thread's priority so Whisper inference doesn't starve
        # foreground apps (e.g. games).  THREAD_PRIORITY_BELOW_NORMAL = -1.
        try:
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), -1
            )
        except Exception:
            pass
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
                if attempt_device == "cpu" and self.model_size in ("large", "large-v2"):
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

                model = WhisperModel(
                    _resolve_model_path(self.model_size),
                    device=attempt_device,
                    compute_type=attempt_ct,
                    # Limit CPU threads used for pre/post-processing work that
                    # runs even in GPU mode.  2 threads is enough; more just
                    # takes CPU time away from games/foreground apps.
                    cpu_threads=2,
                    num_workers=1,
                )
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

        utterance_seq = 0
        SAMPLE_RATE = 16_000
        SILENCE_GAP = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)

        while not self._stop_event.is_set():
            try:
                item = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            tag = item[0]
            payload = item[1]
            source_id = item[2] if len(item) > 2 else 1

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
            queued_audio: list = []  # list of (audio_data, source_id)
            while True:
                try:
                    next_item = self.audio_queue.get_nowait()
                except queue.Empty:
                    break
                next_tag = next_item[0]
                next_payload = next_item[1]
                next_src = next_item[2] if len(next_item) > 2 else 1
                if next_tag == "stop":
                    self._stop_event.set()
                    break
                if next_tag == "error":
                    self.on_error(next_payload)
                    break
                if next_tag == "audio":
                    queued_audio.append((next_payload, next_src))
                # status tags discarded (stale by now)

            # audio_parts is list of (audio_data, source_id)
            # Only batch items from the same source to avoid cross-source merging.
            audio_parts = [(payload, source_id)] + queued_audio
            seq_start = utterance_seq + 1
            utterance_seq += len(audio_parts)
            seq_end = utterance_seq
            seq_label = f"#{seq_start}" if seq_start == seq_end else f"#{seq_start}–{seq_end}"


            # Group consecutive same-source items; process each group separately.
            groups: list[tuple[int, list]] = []  # (source_id, [audio_arrays])
            for aud, src in audio_parts:
                if groups and groups[-1][0] == src:
                    groups[-1][1].append(aud)
                else:
                    groups.append((src, [aud]))

            for grp_src, grp_parts in groups:
                if len(grp_parts) == 1:
                    audio_data = grp_parts[0]
                else:
                    interleaved = []
                    for i, part in enumerate(grp_parts):
                        interleaved.append(part)
                        if i < len(grp_parts) - 1:
                            interleaved.append(SILENCE_GAP)
                    audio_data = np.concatenate(interleaved)

                duration = len(audio_data) / SAMPLE_RATE
                try:
                    log.debug("Transcribing %s src=%d (%.2f s)", seq_label, grp_src, duration)
                    self.on_status(f"Translating {seq_label} ({duration:.1f}s)…")

                    segments, info = model.transcribe(
                        audio_data,
                        task="translate",       # outputs English directly
                        beam_size=1,            # greedy decode — no beam search overhead
                        temperature=0.0,        # single pass only; disables the fallback
                                                # retry loop (0.0→0.2→0.4→0.6→0.8→1.0)
                                                # that was doing 6× the GPU work per utterance
                        log_prob_threshold=None,        # don't trigger temperature fallback
                        no_speech_threshold=0.6,        # skip silent/noise segments fast
                        condition_on_previous_text=False,  # no prompt carry-over overhead
                        without_timestamps=True,           # skip timestamp decode pass
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 300},
                        repetition_penalty=1.3,    # penalise repeated tokens — suppresses
                                                   # "Come on, come on, come on..." loops
                    )
                    log.debug(
                        "%s — language=%s (prob=%.2f)",
                        seq_label, info.language, info.language_probability
                    )
                    for segment in segments:
                        text = segment.text.strip()
                        if not text:
                            continue
                        # ── Hallucination filters ────────────────────────────────
                        if _is_repetition_loop(text):
                            continue
                        if _is_phantom(
                            text,
                            getattr(segment, "no_speech_prob", 0.0),
                            getattr(segment, "avg_logprob", 0.0),
                        ):
                            continue
                        # ───────────────────────────────────────────────
                        ts = time.strftime("%H:%M:%S")
                        log.info("[%s] %s %s", ts, seq_label, text)
                        self.on_result(ts, text, info.language, grp_src)
                    # Never break out of the segment generator mid-inference.
                    # Interrupting the lazy ctranslate2 generator leaves its
                    # internal C++ decoder state allocated; when the model is
                    # later GC'd the destructor crashes with std::terminate()
                    # (exception code 0xc0000409 in ucrtbase.dll).  Instead we
                    # let the current utterance finish, then the outer while
                    # loop's stop-event check exits cleanly.
                    self.on_status("Listening…")
                    # Yield GPU time back to foreground apps between inferences.
                    # 150 ms is imperceptible as translation latency but gives the
                    # OS GPU scheduler a window to service the game.
                    time.sleep(0.15)
                except Exception as exc:  # noqa: BLE001
                    err_str = str(exc).lower()
                    # mkl_malloc failure = Intel MKL can't get RAM — happens when the
                    # game's loading screen temporarily consumes most system RAM.
                    # Transient: wait for loading to finish, skip utterance, continue.
                    ram_pressure = "mkl_malloc" in err_str or "mkl-service" in err_str
                    cuda_failure = device == "cuda" and not ram_pressure and (
                        "dll" in err_str
                        or "library" in err_str
                        or "out of memory" in err_str
                        or "bad allocation" in err_str
                        or isinstance(exc, MemoryError)
                    )
                    if ram_pressure:
                        log.warning("RAM pressure on %s (%s) — waiting 2 s then retrying", seq_label, exc)
                        self.on_status("Low RAM (game loading?) — pausing, will retry…")
                        time.sleep(2.0)  # wait for loading screen to release RAM
                        # Re-queue the utterance so it gets translated once RAM frees up.
                        # The audio data itself is safe — it lives in Python's heap, not
                        # MKL's allocation arena.  Only MKL's internal scratchpad failed.
                        self.audio_queue.put(("audio", audio_data, grp_src))
                        self.on_status("Listening…")
                    elif cuda_failure:
                        # GPU ran out of VRAM (game took it all) or CUDA DLL missing.
                        # Fall back to CPU silently and keep the session running —
                        # do NOT call on_error() which would stop the app.
                        log.warning("CUDA failure on %s (%s) — falling back to CPU", seq_label, exc)
                        self.on_status("VRAM full — switching to CPU and retrying…")
                        try:
                            # Free VRAM explicitly before parking. unload_model() releases
                            # the ctranslate2 GPU allocations without running the crashing
                            # C++ destructor path.  The empty object is then parked so
                            # the (now no-op) destructor never runs from the GC either.
                            try:
                                model.model.unload_model(to_cpu=False)
                                log.debug("CUDA model unloaded from VRAM before CPU fallback")
                            except Exception:
                                pass
                            _RETIRED_MODELS.append(model)  # suppress crashing destructor
                            model = WhisperModel(
                                _resolve_model_path(self.model_size), device="cpu", compute_type="int8",
                                cpu_threads=2, num_workers=1,
                            )
                            device = "cpu"
                            log.warning("Permanently switched to CPU (int8) after CUDA failure")
                            self.on_device_info("cpu", "int8")
                            self.on_status("Switched to CPU (VRAM full). Translation continuing…")
                            # Re-queue this batch so it gets translated on CPU
                            self.audio_queue.put(("audio", audio_data, grp_src))
                        except Exception as cpu_exc:  # noqa: BLE001
                            log.exception("CPU fallback also failed")
                            self.on_error(f"Fatal transcription error: {cpu_exc}")
                    else:
                        log.exception("Transcription error on %s", seq_label)
                        self.on_error(f"Transcription error: {exc}")

        # Free VRAM and park the model shell.  unload_model() releases the GPU
        # allocations explicitly, so VRAM is returned immediately on Stop.
        # The now-empty Python object is parked in _RETIRED_MODELS to prevent
        # ctranslate2's crashing C++ destructor from ever running.  CPU models
        # are let go normally — their destructors are safe and free ~1.5 GB RAM.
        try:
            if device == "cuda":
                try:
                    model.model.unload_model(to_cpu=False)
                    log.debug("CUDA model VRAM freed via unload_model()")
                except Exception:
                    pass
                _RETIRED_MODELS.append(model)
                log.debug("CUDA model shell parked in _RETIRED_MODELS (destructor suppressed)")
            else:
                log.debug("CPU model released to GC")
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
            log.info("Device forced to CUDA (int8_float16) by user")
            return "cuda", "int8_float16"
        # Auto: prefer CUDA with int8_float16 — significantly lighter on VRAM
        # and GPU compute than float16, with negligible quality difference.
        try:
            import torch  # type: ignore[import]
            if torch.cuda.is_available():
                log.info("CUDA available via torch — using GPU (int8_float16)")
                return "cuda", "int8_float16"
        except ImportError:
            pass
        try:
            import ctranslate2
            supported = ctranslate2.get_supported_compute_types("cuda")
            if supported:
                compute_type = "int8_float16" if "int8_float16" in supported else "int8"
                log.info(
                    "CUDA available via ctranslate2 (compute_type=%s, all supported: %s)",
                    compute_type, supported
                )
                return "cuda", compute_type
        except Exception as exc:  # noqa: BLE001
            log.warning("ctranslate2 CUDA probe failed: %s", exc)
        log.warning("CUDA not available — falling back to CPU (int8)")
        return "cpu", "int8"
