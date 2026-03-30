"""
audio_capture.py
Captures system/loopback audio via Windows WASAPI and pushes
numpy float32 chunks (16 kHz mono) into a queue for transcription.
"""

import threading
import queue
import numpy as np

from logger import get_logger

try:
    import soundcard as sc
except ImportError:
    sc = None  # handled at runtime

log = get_logger(__name__)

# Whisper expects 16 kHz mono float32
SAMPLE_RATE = 16_000

# ── VAD constants ─────────────────────────────────────────────────────────────
# Each VAD decision is made on 30 ms frames.
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)   # 480 samples

# Ring buffer kept BEFORE the first speech frame is detected, so the
# beginning of a sentence is not clipped.
PRE_ROLL_MS = 300
PRE_ROLL_FRAMES = int(PRE_ROLL_MS / FRAME_MS)         # 10 frames

# How long silence must persist (after speech) before we consider the
# utterance finished and ship it for translation.
# This is the *base* (minimum) gate — for short utterances it may scale up.
END_SILENCE_MS = 300
END_SILENCE_FRAMES = int(END_SILENCE_MS / FRAME_MS)   # 10 frames

# For longer utterances the silence gate scales up so a brief pause mid-sentence
# doesn't prematurely cut off a fast speaker (e.g. Spanish).
# Every SCALE_PER_SEC_MS of speech adds SCALE_STEP_MS to the gate, capped at
# END_SILENCE_MAX_MS.
SCALE_PER_SEC_MS  = 3_000   # add extra time for every 3 s of speech so far
SCALE_STEP_MS     = 150     # extra ms added per 3 s block
END_SILENCE_MAX_MS = 900    # hard ceiling

# When in TRAILING state, how many *consecutive* speech frames are needed
# to flip back to SPEAKING.  A single micro-blip (e.g. VoiceMeeter noise
# floor) won't reset the silence counter; sustained audio will.
RESUME_SPEECH_FRAMES = 3   # 3 × 30 ms = 90 ms

# Safety cap: force-emit an utterance if it grows beyond this duration,
# even if the speaker hasn't paused yet.
MAX_UTTERANCE_MS = 20_000
MAX_UTTERANCE_FRAMES = int(MAX_UTTERANCE_MS / FRAME_MS)   # 667 frames

# Minimum speech duration to treat a trigger as a real utterance rather
# than a brief noise burst.
MIN_SPEECH_MS = 200
MIN_SPEECH_FRAMES = int(MIN_SPEECH_MS / FRAME_MS)     # 7 frames

# RMS energy threshold (float32 normalised −1…1) above which a 30 ms
# frame is classified as speech.  Increase if background noise causes
# false triggers; decrease if quiet speech is missed.
SPEECH_THRESHOLD = 0.015

# Internal VAD states
_SILENT = 0
_SPEAKING = 1
_TRAILING = 2


def list_loopback_devices() -> list[str]:
    """Return display names of available WASAPI loopback sources."""
    if sc is None:
        log.warning("soundcard not installed — cannot enumerate loopback devices")
        return []
    devices = [str(m.name) for m in sc.all_microphones(include_loopback=True)]
    log.debug("Found %d loopback device(s): %s", len(devices), devices)
    return devices


def get_default_output_name() -> str | None:
    """
    Return the name of the Windows default audio output device, or None if
    it cannot be determined.
    """
    if sc is None:
        return None
    try:
        default = sc.default_speaker()
        name = str(default.name)
        log.debug("Default output device: %r", name)
        return name
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not detect default output device: %s", exc)
        return None


def get_loopback_device(name: str | None = None):
    """
    Return a soundcard microphone object for the given loopback device name.
    If *name* is None or not found, returns the system default loopback.
    """
    if sc is None:
        raise RuntimeError("soundcard is not installed.")
    all_lb = sc.all_microphones(include_loopback=True)
    if not all_lb:
        raise RuntimeError(
            "No loopback devices found. "
            "Ensure 'Stereo Mix' or a virtual audio cable is enabled in Windows Sound settings."
        )
    if name:
        for m in all_lb:
            if m.name == name:
                return m
    # Fall back to first loopback device
    return all_lb[0]


class AudioCaptureThread(threading.Thread):
    """
    Background thread that reads loopback audio and uses a VAD state
    machine to detect complete utterances (speech → silence boundary).

    When an utterance ends (speaker pauses for END_SILENCE_MS milliseconds),
    the accumulated float32 numpy array is put into *audio_queue* so Whisper
    receives a complete sentence rather than an arbitrary chunk.

    Queue message types:
        ("audio",  np.ndarray)  — a complete utterance ready for translation
        ("status", str)         — informational status for the GUI status bar
        ("error",  str)         — fatal error; thread will exit
    """

    def __init__(self, audio_queue: queue.Queue, device_name: str | None = None,
                 speech_threshold: float = SPEECH_THRESHOLD,
                 end_silence_ms: int = END_SILENCE_MS,
                 on_level=None,
                 source_id: int = 1):
        super().__init__(daemon=True, name=f"AudioCaptureThread-S{source_id}")
        self.audio_queue = audio_queue
        self.device_name = device_name
        self.speech_threshold = speech_threshold
        self.end_silence_ms = end_silence_ms  # base gate; may scale up adaptively
        self._on_level = on_level  # callable(rms: float) or None
        self.source_id = source_id
        self._level_frame_count = 0
        self._stop_event = threading.Event()
        self._stop_event.set()

    def run(self):
        log.info("AudioCaptureThread starting (device=%r)", self.device_name)
        # Lower this thread's priority so the OS always gives the game/GPU
        # driver first access to CPU time.  THREAD_PRIORITY_BELOW_NORMAL = -1.
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), -1
            )
        except Exception:
            pass
        try:
            device = get_loopback_device(self.device_name)
        except RuntimeError as exc:
            log.error("Device selection failed: %s", exc)
            self.audio_queue.put(("error", str(exc)))
            return

        # Raw read block size; smaller = more responsive stop detection.
        block_size = 512
        # Accumulates sub-frame samples between reads.
        raw_buf = np.empty(0, dtype=np.float32)

        # VAD state machine
        import collections
        state = _SILENT
        pre_roll = collections.deque(maxlen=PRE_ROLL_FRAMES)  # ring buffer
        utterance_frames: list[np.ndarray] = []
        speech_frame_count = 0
        silence_frame_count = 0
        trail_speech_run = 0   # consecutive speech frames seen while trailing

        def _emit():
            """Concatenate accumulated frames and ship them for translation."""
            nonlocal utterance_frames, speech_frame_count, silence_frame_count, state, trail_speech_run
            trail_speech_run = 0
            if utterance_frames and speech_frame_count >= MIN_SPEECH_FRAMES:
                audio = np.concatenate(utterance_frames)
                log.info(
                    "Utterance emitted: %.2f s (%d speech frames)",
                    len(audio) / SAMPLE_RATE, speech_frame_count
                )
                self.audio_queue.put(("audio", audio, self.source_id))
                self.audio_queue.put(("status", "Processing…", self.source_id))
            else:
                log.debug("Discarded short noise burst (%d speech frames)", speech_frame_count)
            utterance_frames = []
            speech_frame_count = 0
            silence_frame_count = 0
            state = _SILENT

        log.info("Opening recorder on device %r at %d Hz", device.name, SAMPLE_RATE)
        try:
            with device.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=block_size) as rec:
                log.info("Recording started — VAD active")
                self.audio_queue.put(("status", "Listening…", self.source_id))

                while not self._stop_event.is_set():
                    block = rec.record(numframes=block_size)
                    # shape: (block_size, channels) → mono float32
                    mono = block[:, 0].astype(np.float32)
                    raw_buf = np.concatenate([raw_buf, mono])

                    # Process as many complete 30 ms frames as available.
                    while len(raw_buf) >= FRAME_SAMPLES:
                        frame = raw_buf[:FRAME_SAMPLES]
                        raw_buf = raw_buf[FRAME_SAMPLES:]

                        rms = float(np.sqrt(np.mean(frame ** 2)))
                        is_speech = rms > self.speech_threshold
                        # Notify level meter — throttled to every 2nd frame (~15 fps)
                        if self._on_level is not None:
                            self._level_frame_count += 1
                            if self._level_frame_count % 2 == 0:
                                self._on_level(rms)

                        if state == _SILENT:
                            pre_roll.append(frame)
                            if is_speech:
                                # Speech started — prepend pre-roll so the
                                # opening consonants aren't clipped.
                                utterance_frames = [f.copy() for f in pre_roll]
                                speech_frame_count = 1
                                silence_frame_count = 0
                                state = _SPEAKING
                                self.audio_queue.put(("status", "Speech detected…", self.source_id))
                                log.debug("VAD: SILENT → SPEAKING (rms=%.4f)", rms)

                        elif state == _SPEAKING:
                            utterance_frames.append(frame)
                            if is_speech:
                                speech_frame_count += 1
                                silence_frame_count = 0
                            else:
                                silence_frame_count = 1
                                state = _TRAILING
                                log.debug("VAD: SPEAKING → TRAILING")
                            # Safety cap
                            if len(utterance_frames) >= MAX_UTTERANCE_FRAMES:
                                log.info("VAD: max utterance length reached — force emit")
                                _emit()

                        elif state == _TRAILING:
                            utterance_frames.append(frame)
                            if is_speech:
                                trail_speech_run += 1
                                if trail_speech_run >= RESUME_SPEECH_FRAMES:
                                    # Sustained audio — genuinely resuming speech
                                    speech_frame_count += trail_speech_run
                                    silence_frame_count = 0
                                    trail_speech_run = 0
                                    state = _SPEAKING
                                    log.debug("VAD: TRAILING → SPEAKING (resumed)")
                                # else: micro-blip, stay trailing, don't reset silence counter
                            else:
                                trail_speech_run = 0
                                silence_frame_count += 1
                                # Adaptive gate: scale end-silence up with utterance length
                                utterance_ms = len(utterance_frames) * FRAME_MS
                                scale_steps = int(utterance_ms / SCALE_PER_SEC_MS)
                                adaptive_ms = min(
                                    self.end_silence_ms + scale_steps * SCALE_STEP_MS,
                                    END_SILENCE_MAX_MS
                                )
                                adaptive_frames = max(2, int(adaptive_ms / FRAME_MS))
                                if silence_frame_count >= adaptive_frames:
                                    log.debug(
                                        "VAD: silence gate reached (%d ms, utterance %.1f s)",
                                        adaptive_ms, utterance_ms / 1000
                                    )
                                    _emit()
                                    self.audio_queue.put(("status", "Listening…", self.source_id))
                            # Safety cap
                            if len(utterance_frames) >= MAX_UTTERANCE_FRAMES:
                                log.info("VAD: max utterance length reached — force emit")
                                _emit()
                                self.audio_queue.put(("status", "Listening…", self.source_id))

        except Exception as exc:  # noqa: BLE001
            log.exception("Fatal capture error")
            self.audio_queue.put(("error", f"Capture error: {exc}", self.source_id))
        finally:
            log.info("AudioCaptureThread stopped")
