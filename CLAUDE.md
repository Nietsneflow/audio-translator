# Audio Translate

Windows desktop app that captures system audio or microphone input, transcribes speech with faster-whisper (ctranslate2), and outputs translated English text in real time to the screen and to text files used as OBS/stream overlays.

## File map

| File | Role |
|------|------|
| `main.py` | Entry point: dependency checks, priority class, launches `gui.App` |
| `gui.py` | Tkinter UI — toolbar, dual transcript panes, options menu, level meter, rolling output-file writes |
| `transcriber.py` | Drains audio queue (3-tuple: tag/payload/source_id) → faster-whisper → hallucination filter (`_is_phantom`, `_PHANTOM_PHRASES`) → fires callback with English text |
| `audio_capture.py` | soundcard loopback/mic capture + multi-stage adaptive VAD (pre-roll, threshold, adaptive silence gate with duration scaling, min-speech guard, resume-speech frames, max-utterance cap) |
| `logger.py` | Logging setup, routes to `logs/` |
| `download_translation_model.py` | One-time download of argostranslate en→ru language pack |
| `setup.bat` / `Update.bat` / `Repair.bat` | Windows installer/updater scripts |
| `Source1.txt`, `Source2.txt`, `output.txt` | Live output files (last N lines, overwritten each run) |
| `models/small/`, `models/medium/` | Bundled CTranslate2 Whisper models — loaded from disk, no internet needed |
| `config.json` | Persisted user settings |

## Tech stack & hard constraints

- Python 3.11, Windows only. Do not introduce cross-platform abstractions — paths, DLL loading, priority class, and audio device enumeration are intentionally Windows-specific.
- faster-whisper (ctranslate2) for speech recognition; models live in `models/`.
- soundcard for audio capture (loopback/Stereo Mix and microphone).
- tkinter for the GUI — no external UI frameworks.
- argostranslate for optional offline en→ru back-translation.
- NVIDIA GPU (CUDA 12) via torch + nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels; falls back to CPU automatically.
- CUDA DLL directories are registered at runtime via `os.add_dll_directory()` — keep `_DLL_DIR_HANDLES` alive.
- Retired GPU models must never be destroyed on a background thread (ctranslate2 destructor bug) — use the `_RETIRED_MODELS` list pattern in `transcriber.py`.
- Output files hold only the last 20 (per-source) or 40 (combined) entries — always overwritten with a rolling window, never appended.
- Process priority is `BELOW_NORMAL` so the app doesn't starve foreground apps (games, video).

## Development principles

- Minimal changes: only touch what's necessary for the requested feature or fix.
- No new dependencies unless explicitly approved — the install footprint is already large.
- Thread safety: audio capture and transcription run on daemon threads; UI updates must happen on the main tkinter thread via `after()` — never call tkinter from a background thread.
- All settings persist to `config.json` and are restored on launch. New settings must have safe defaults so existing installs don't break.
- Use `get_logger(__name__)` from `logger.py` for all logging.
- If a change needs a new dependency or install step, update `requirements.txt` and the setup scripts explicitly.

## Common tasks

- **New UI option**: add toggle to Options menu in `gui.py`, persist in `config.json`, wire callback.
- **VAD tuning**: all VAD constants live at the top of `audio_capture.py` — `SPEECH_THRESHOLD`, `END_SILENCE_MS`, `END_SILENCE_MAX_MS`, `SCALE_PER_SEC_MS`, `PRE_ROLL_MS`, `MIN_SPEECH_FRAMES`, `RESUME_SPEECH_FRAMES`, `MAX_UTTERANCE_MS`; per-source UI controls in `gui.py`.
- **Model change / new size**: update `MODEL_OPTIONS` and `DEFAULT_MODEL_LABEL` in `transcriber.py`; the Model dropdown in `gui.py` is populated from those exports.
- **New output format**: extend the rolling-write logic in `gui.py`; update `readme.txt`.
- **Transcription bug**: check the audio queue drain loop, segment iteration, and ctranslate2 model load in `transcriber.py`.
- **CUDA not detected**: check `_register_cuda_dll_dirs()` in `transcriber.py` and pynvml availability.
