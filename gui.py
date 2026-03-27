"""
gui.py
tkinter GUI for the Russian → English live translator.
Provides: device selector, model selector, start/stop button,
always-on-top toggle, and a scrolling caption area.
"""

import collections
import json
import os
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

try:
    import psutil as _psutil  # type: ignore[import]
except ImportError:
    _psutil = None  # type: ignore

try:
    import pynvml as _pynvml  # type: ignore[import]
    _pynvml.nvmlInit()
    _NVML_OK = True
except Exception:
    _NVML_OK = False

from logger import get_logger, open_session_log, close_session_log
from audio_capture import list_loopback_devices, get_default_output_name, AudioCaptureThread, SPEECH_THRESHOLD, END_SILENCE_MS
from transcriber import TranscriberThread, MODEL_OPTIONS, DEFAULT_MODEL_LABEL

log = get_logger(__name__)

# ─── colour scheme ────────────────────────────────────────────────────────────
BG = "#1e1e2e"
FG = "#cdd6f4"
ACCENT = "#89b4fa"
BTN_START = "#a6e3a1"
BTN_STOP = "#f38ba8"
ENTRY_BG = "#313244"
TEXT_BG = "#181825"
STATUS_FG = "#a6adc8"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Russian → English Live Translator")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(640, 420)

        self._audio_queue: queue.Queue = queue.Queue()  # unbounded — capture never blocks
        self._capture_thread: AudioCaptureThread | None = None
        self._transcriber_thread: TranscriberThread | None = None
        self._running = False
        self._poll_id: str | None = None  # after() handle for queue-depth polling
        self._stats_poll_id: str | None = None  # after() handle for stats polling
        self._device_info: tuple[str, str] | None = None  # (device, compute_type)
        self._output_lines: collections.deque = collections.deque(maxlen=20)
        self._output_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output.txt"
        )
        self._config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json"
        )

        saved_threshold = self._load_config().get("threshold", SPEECH_THRESHOLD)
        self._threshold_var = tk.DoubleVar(value=saved_threshold)
        self._threshold_display_var = tk.StringVar(value=f"{saved_threshold:.3f}")
        saved_silence = int(self._load_config().get("end_silence_ms", END_SILENCE_MS))
        self._silence_var = tk.IntVar(value=saved_silence)
        self._silence_display_var = tk.StringVar(value=f"{saved_silence} ms")
        self._meter_visible = False
        self._meter_peak = 0.0

        _cfg = self._load_config()
        self._saved_model = _cfg.get("model", DEFAULT_MODEL_LABEL)
        self._saved_processor = _cfg.get("processor", "Auto")
        self._saved_device = _cfg.get("device", None)

        self._build_ui()
        self._refresh_devices()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── top controls bar ──────────────────────────────────────────────────
        ctrl = tk.Frame(self, bg=BG, pady=8, padx=10)
        ctrl.pack(fill=tk.X)

        # Columns 1 (device combo) and 4 (model combo) absorb spare width.
        # Weight 2 vs 1 gives the device combo roughly twice the extra space.
        ctrl.columnconfigure(1, weight=2)
        ctrl.columnconfigure(4, weight=1)

        # Device selector
        tk.Label(ctrl, text="Audio device:", bg=BG, fg=FG).grid(
            row=0, column=0, sticky=tk.W
        )
        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(
            ctrl, textvariable=self._device_var, state="readonly"
        )
        self._device_combo.grid(row=0, column=1, padx=(4, 4), sticky=tk.EW)
        self._device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        refresh_btn = tk.Button(
            ctrl, text="↺", bg=ENTRY_BG, fg=ACCENT, relief=tk.FLAT,
            command=self._refresh_devices, cursor="hand2"
        )
        refresh_btn.grid(row=0, column=2, padx=(0, 12))

        # Model selector
        tk.Label(ctrl, text="Model:", bg=BG, fg=FG).grid(
            row=0, column=3, sticky=tk.W
        )
        self._model_var = tk.StringVar(value=self._saved_model)
        self._model_combo = ttk.Combobox(
            ctrl, textvariable=self._model_var,
            values=list(MODEL_OPTIONS.keys()), state="readonly"
        )
        self._model_combo.grid(row=0, column=4, padx=(4, 12), sticky=tk.EW)
        self._model_combo.bind("<<ComboboxSelected>>", lambda _: self._save_config())

        # Processor selector
        tk.Label(ctrl, text="Processor:", bg=BG, fg=FG).grid(
            row=0, column=5, sticky=tk.W
        )
        self._processor_var = tk.StringVar(value=self._saved_processor)
        self._proc_combo = ttk.Combobox(
            ctrl, textvariable=self._processor_var,
            values=["Auto", "GPU", "CPU"], state="readonly", width=6
        )
        self._proc_combo.grid(row=0, column=6, padx=(4, 12), sticky=tk.W)
        self._proc_combo.bind("<<ComboboxSelected>>", lambda _: self._save_config())

        # Always-on-top toggle
        self._ontop_var = tk.BooleanVar(value=False)
        ontop_cb = tk.Checkbutton(
            ctrl, text="Always on top", variable=self._ontop_var,
            bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG,
            command=self._toggle_ontop
        )
        ontop_cb.grid(row=0, column=7, padx=(0, 12), sticky=tk.W)

        # Start / Stop button
        self._toggle_btn = tk.Button(
            ctrl, text="▶  Start", bg=BTN_START, fg="#1e1e2e",
            font=("Segoe UI", 10, "bold"), relief=tk.FLAT,
            padx=14, pady=4, cursor="hand2", command=self._toggle
        )
        self._toggle_btn.grid(row=0, column=8, padx=4, sticky=tk.E)

        # Clear button
        clear_btn = tk.Button(
            ctrl, text="Clear", bg=ENTRY_BG, fg=FG, relief=tk.FLAT,
            padx=10, pady=4, cursor="hand2", command=self._clear_text
        )
        clear_btn.grid(row=0, column=9, padx=(0, 4), sticky=tk.E)

        # Meter toggle button
        self._meter_btn = tk.Button(
            ctrl, text="◎ Meter", bg=ENTRY_BG, fg=FG, relief=tk.FLAT,
            padx=8, pady=4, cursor="hand2", command=self._toggle_meter
        )
        self._meter_btn.grid(row=0, column=10, padx=(0, 4), sticky=tk.E)

        # ── content area: transcript + optional meter panel ───────────────────
        self._content_frame = tk.Frame(self, bg=BG)
        self._content_frame.pack(fill=tk.BOTH, expand=True)

        # Wrap the transcript in a relative frame so we can overlay a clear button
        text_frame = tk.Frame(self._content_frame, bg=BG)
        text_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._text = scrolledtext.ScrolledText(
            text_frame, bg=TEXT_BG, fg=FG, font=("Segoe UI", 13),
            wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT,
            padx=12, pady=10, insertbackground=FG
        )
        self._text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        # Small floating clear button anchored to bottom-right of the transcript
        tk.Button(
            text_frame, text="✕ clear", bg="#2a2a3e", fg="#585b70",
            font=("Segoe UI", 8), relief=tk.FLAT, padx=6, pady=2,
            cursor="hand2", command=self._clear_text,
            activebackground="#313244", activeforeground=FG,
        ).place(relx=1.0, rely=1.0, x=-18, y=-8, anchor="se")

        # Tag for timestamp colour
        self._text.tag_configure("ts", foreground=ACCENT, font=("Segoe UI", 10))
        self._text.tag_configure("txt", foreground=FG, font=("Segoe UI", 13))

        # ── audio level meter panel (right side, toggled) ─────────────────────
        self._meter_panel = tk.Frame(self._content_frame, bg="#13131e", width=130)
        self._meter_panel.pack_propagate(False)
        # not packed yet — shown/hidden by _toggle_meter()

        tk.Label(self._meter_panel, text="LEVEL METER", bg="#13131e", fg=ACCENT,
                 font=("Segoe UI", 8, "bold"), pady=4).pack()

        self._meter_canvas = tk.Canvas(
            self._meter_panel, bg="#0d0d1a", highlightthickness=0, width=110, height=180
        )
        self._meter_canvas.pack(padx=8, pady=(0, 4))

        self._meter_rms_var = tk.StringVar(value="RMS: 0.000")
        tk.Label(self._meter_panel, textvariable=self._meter_rms_var,
                 bg="#13131e", fg=FG, font=("Segoe UI", 8)).pack()

        tk.Frame(self._meter_panel, bg="#313244", height=1).pack(fill=tk.X, padx=8, pady=6)
        tk.Label(self._meter_panel, text="SENSITIVITY", bg="#13131e", fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).pack()
        tk.Label(self._meter_panel, text="▲ more", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()

        tk.Scale(
            self._meter_panel,
            variable=self._threshold_var,
            from_=0.001, to=0.080,
            resolution=0.001,
            orient=tk.VERTICAL,
            bg="#13131e", fg=FG, troughcolor=ENTRY_BG,
            highlightthickness=0,
            command=self._on_threshold_change,
            length=100,
            showvalue=False,
        ).pack(pady=2)

        tk.Label(self._meter_panel, text="▼ less", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Label(self._meter_panel, textvariable=self._threshold_display_var,
                 bg="#13131e", fg="#89b4fa", font=("Segoe UI", 9, "bold")).pack()
        tk.Label(self._meter_panel, text="threshold", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()

        tk.Frame(self._meter_panel, bg="#313244", height=1).pack(fill=tk.X, padx=8, pady=6)
        tk.Label(self._meter_panel, text="PAUSE GATE", bg="#13131e", fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).pack()
        tk.Label(self._meter_panel, text="longer ▲", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Scale(
            self._meter_panel,
            variable=self._silence_var,
            from_=600, to=50,
            resolution=50,
            orient=tk.VERTICAL,
            bg="#13131e", fg=FG, troughcolor=ENTRY_BG,
            highlightthickness=0,
            command=self._on_silence_change,
            length=80,
            showvalue=False,
        ).pack(pady=2)
        tk.Label(self._meter_panel, text="shorter ▼", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Label(self._meter_panel, textvariable=self._silence_display_var,
                 bg="#13131e", fg="#89b4fa", font=("Segoe UI", 9, "bold")).pack()
        tk.Label(self._meter_panel, text="end-silence", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()

        # ── status bar ────────────────────────────────────────────────────────
        status_frame = tk.Frame(self, bg=ENTRY_BG)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        status_frame.columnconfigure(0, weight=1)

        # ── performance stats bar (above status bar) ──────────────────────────
        stats_frame = tk.Frame(self, bg="#1a1a2e")
        stats_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self._stats_cpu_var = tk.StringVar(value="")
        self._stats_ram_var = tk.StringVar(value="")
        self._stats_gpu_var = tk.StringVar(value="")
        self._stats_vram_var = tk.StringVar(value="")

        lbl_kw = dict(bg="#1a1a2e", fg="#6c7086", font=("Segoe UI", 8), padx=8, pady=2)
        val_kw = dict(bg="#1a1a2e", fg="#cdd6f4", font=("Segoe UI", 8, "bold"), padx=2, pady=2)
        sep_kw = dict(bg="#313244", width=1)

        tk.Label(stats_frame, text="CPU", **lbl_kw).pack(side=tk.LEFT)
        tk.Label(stats_frame, textvariable=self._stats_cpu_var, **val_kw).pack(side=tk.LEFT)
        tk.Frame(stats_frame, **sep_kw).pack(side=tk.LEFT, fill=tk.Y, pady=3)
        tk.Label(stats_frame, text="RAM", **lbl_kw).pack(side=tk.LEFT)
        tk.Label(stats_frame, textvariable=self._stats_ram_var, **val_kw).pack(side=tk.LEFT)
        tk.Frame(stats_frame, **sep_kw).pack(side=tk.LEFT, fill=tk.Y, pady=3)
        tk.Label(stats_frame, text="GPU", **lbl_kw).pack(side=tk.LEFT)
        tk.Label(stats_frame, textvariable=self._stats_gpu_var, **val_kw).pack(side=tk.LEFT)
        tk.Frame(stats_frame, **sep_kw).pack(side=tk.LEFT, fill=tk.Y, pady=3)
        tk.Label(stats_frame, text="VRAM", **lbl_kw).pack(side=tk.LEFT)
        tk.Label(stats_frame, textvariable=self._stats_vram_var, **val_kw).pack(side=tk.LEFT)

        self._poll_stats()  # start immediately

        self._status_var = tk.StringVar(value="Ready.")
        tk.Label(
            status_frame, textvariable=self._status_var,
            bg=ENTRY_BG, fg=STATUS_FG, anchor=tk.W, padx=10, pady=4
        ).grid(row=0, column=0, sticky=tk.EW)

        # Separator
        tk.Frame(status_frame, bg="#45475a", width=1).grid(
            row=0, column=1, sticky=tk.NS, pady=3
        )

        # GPU / CPU chip
        self._hw_var = tk.StringVar(value="")
        self._hw_label = tk.Label(
            status_frame, textvariable=self._hw_var,
            bg=ENTRY_BG, fg=STATUS_FG, anchor=tk.CENTER,
            padx=10, pady=4, width=12, font=("Segoe UI", 9, "bold")
        )
        self._hw_label.grid(row=0, column=2, sticky=tk.E)

        # Separator before queue counter
        tk.Frame(status_frame, bg="#45475a", width=1).grid(
            row=0, column=3, sticky=tk.NS, pady=3
        )

        self._queue_var = tk.StringVar(value="")
        tk.Label(
            status_frame, textvariable=self._queue_var,
            bg=ENTRY_BG, fg=STATUS_FG, anchor=tk.E, padx=12, pady=4,
            width=22
        ).grid(row=0, column=4, sticky=tk.E)

    # ── control helpers ───────────────────────────────────────────────────────

    def _refresh_devices(self):
        devices = list_loopback_devices()
        if not devices:
            devices = ["(no loopback devices found)"]
            self._device_combo["values"] = devices
            self._device_combo.current(0)
            return

        self._device_combo["values"] = devices

        # 1. Try to restore the saved device name.
        saved = getattr(self, "_saved_device", None)
        if saved:
            for i, name in enumerate(devices):
                if name == saved:
                    self._device_combo.current(i)
                    log.debug("Restored saved device index %d: %r", i, name)
                    return

        # 2. Fall back to matching the Windows default output device.
        default_name = get_default_output_name()
        if default_name:
            for i, name in enumerate(devices):
                if default_name.lower() in name.lower() or name.lower() in default_name.lower():
                    self._device_combo.current(i)
                    log.debug("Pre-selected default device index %d: %r", i, name)
                    return

        # 3. Fall back to first entry.
        self._device_combo.current(0)

    def _on_device_selected(self, _event=None):
        self._save_config()
        if self._running:
            self._restart_capture()

    def _restart_capture(self):
        """Stop the current capture thread and start a new one with the selected device.
        The transcriber thread keeps running — no model reload needed.
        """
        device_name = self._device_var.get()
        if not device_name or "(no loopback" in device_name:
            return
        log.info("Device changed — restarting capture on %r", device_name)
        self._set_status(f"Switching to {device_name}…")

        # Stop old capture thread only
        if self._capture_thread:
            self._capture_thread.stop()
            # Don't join — daemon thread will die on its own; stop() signals it promptly

        # Drain any stale audio buffered from the old device
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._capture_thread = AudioCaptureThread(
            audio_queue=self._audio_queue,
            device_name=device_name,
            speech_threshold=self._threshold_var.get(),
            end_silence_ms=self._silence_var.get(),
            on_level=self._on_level_callback,
        )
        self._capture_thread.start()
        self._meter_peak = 0.0
        log.info("Capture restarted on %r", device_name)

    def _toggle_ontop(self):
        self.attributes("-topmost", self._ontop_var.get())

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        # Guard: if the old transcriber is still alive (e.g. mid model-load from
        # the last session), defer until it has fully exited AND freed its model.
        # Two simultaneous WhisperModel instances can exhaust VRAM/RAM and cause
        # a hard C++ abort in ctranslate2 with no Python traceback.
        if self._transcriber_thread and self._transcriber_thread.is_alive():
            # Disable the button so a second click can't queue a parallel
            # _start() chain that would also race to load the model.
            self._toggle_btn.config(
                text="⏳ Unloading…", bg=ENTRY_BG, state=tk.DISABLED
            )
            self._set_status("Waiting for previous session to finish unloading model…")
            self.after(300, self._start)
            return
        # Restore normal button state (it was disabled/relabelled above).
        self._toggle_btn.config(state=tk.NORMAL)
        # If the thread just died, give it a moment for its in-thread CUDA sync
        # and gc.collect() to finish before we start allocating a new model.
        if self._transcriber_thread is not None:
            self._transcriber_thread.join(timeout=1.0)

        device_name = self._device_var.get()
        open_session_log()
        log.info("Start requested — device=%r model=%r", device_name, self._model_var.get())
        if not device_name or "(no loopback" in device_name:
            log.error("No loopback device selected — cannot start")
            messagebox.showerror(
                "No device",
                "No loopback audio device found.\n\n"
                "Enable 'Stereo Mix' in Windows Sound settings or install a "
                "virtual audio cable (e.g. VB-Audio Virtual Cable)."
            )
            return

        # Replace the queue so any still-lingering old thread can't inject
        # stale audio or stop sentinels into the new session.
        self._audio_queue = queue.Queue()

        self._capture_thread = AudioCaptureThread(
            audio_queue=self._audio_queue,
            device_name=device_name,
            speech_threshold=self._threshold_var.get(),
            end_silence_ms=self._silence_var.get(),
            on_level=self._on_level_callback,
        )
        self._transcriber_thread = TranscriberThread(
            audio_queue=self._audio_queue,
            on_result=self._on_result,
            on_error=self._on_error,
            on_status=self._on_status,
            on_device_info=self._on_device_info,
            model_label=self._model_var.get(),
            force_device=self._processor_var.get(),
        )

        self._capture_thread.start()
        self._transcriber_thread.start()

        self._running = True
        self._toggle_btn.config(text="■  Stop", bg=BTN_STOP)
        self._model_combo.config(state="disabled")
        self._proc_combo.config(state="disabled")
        self._set_status("Starting… (model loading may take a moment on first run)")
        self._start_queue_poll()

    def _stop(self):
        log.info("Stop requested")
        self._stop_queue_poll()
        if self._transcriber_thread:
            self._transcriber_thread.stop()
        if self._capture_thread:
            self._capture_thread.stop()
        self._running = False
        self._device_info = None
        self._queue_var.set("")
        self._hw_var.set("")
        self._hw_label.config(bg=ENTRY_BG)
        self._toggle_btn.config(text="▶  Start", bg=BTN_START)
        self._model_combo.config(state="readonly")
        self._proc_combo.config(state="readonly")
        self._meter_peak = 0.0
        self._meter_rms_var.set("RMS: 0.000")
        close_session_log()

    def _clear_text(self):
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.config(state=tk.DISABLED)

    # ── queue depth polling ───────────────────────────────────────────────────

    def _start_queue_poll(self):
        self._poll_queue_depth()

    def _stop_queue_poll(self):
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None

    def _poll_queue_depth(self):
        """Update the right-side queue depth indicator every 400 ms."""
        depth = self._audio_queue.qsize()
        if depth == 0:
            self._queue_var.set("● idle")
        elif depth == 1:
            self._queue_var.set("● 1 utterance queued")
        else:
            self._queue_var.set(f"● {depth} utterances queued")
        if self._running:
            self._poll_id = self.after(400, self._poll_queue_depth)

    # ── performance stats polling ─────────────────────────────────────────────

    def _poll_stats(self):
        """Kick off a daemon thread to collect stats, then schedule the next poll."""
        import threading
        threading.Thread(target=self._collect_stats, daemon=True).start()
        self._stats_poll_id = self.after(2000, self._poll_stats)

    def _collect_stats(self):
        """Run in a background thread — never blocks the tkinter main loop."""
        cpu_str = ram_str = gpu_str = vram_str = "n/a"

        if _psutil is not None:
            try:
                cpu = _psutil.cpu_percent(interval=None)
                ram = _psutil.virtual_memory()
                cpu_str = f"{cpu:.0f}%"
                ram_str = f"{ram.used / 1024**3:.1f} / {ram.total / 1024**3:.1f} GB"
            except Exception:
                pass

        if _NVML_OK:
            try:
                handle = _pynvml.nvmlDeviceGetHandleByIndex(0)
                util = _pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem = _pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpu_str = f"{util.gpu}%"
                vram_str = f"{mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB"
            except Exception:
                pass

        # Post results back to the main thread
        self.after(0, self._stats_cpu_var.set, cpu_str)
        self.after(0, self._stats_ram_var.set, ram_str)
        self.after(0, self._stats_gpu_var.set, gpu_str)
        self.after(0, self._stats_vram_var.set, vram_str)

    # ── thread-safe callbacks (called from background threads) ────────────────

    def _on_result(self, timestamp: str, text: str):
        """Called from TranscriberThread — must post to main thread via after()."""
        self.after(0, self._append_result, timestamp, text)

    def _on_error(self, message: str):
        self.after(0, self._show_error, message)

    def _on_status(self, message: str):
        self.after(0, self._set_status, message)

    def _on_device_info(self, device: str, compute_type: str):
        self.after(0, self._set_device_chip, device, compute_type)

    # ── main-thread UI updates ────────────────────────────────────────────────

    def _append_result(self, timestamp: str, text: str):
        self._text.config(state=tk.NORMAL)
        self._text.insert(tk.END, f"[{timestamp}] ", "ts")
        self._text.insert(tk.END, f"{text}\n", "txt")
        self._text.see(tk.END)
        self._text.config(state=tk.DISABLED)
        self._output_lines.append(f"[{timestamp}] {text}")
        self._write_output_file()

    def _write_output_file(self):
        try:
            with open(self._output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(self._output_lines))
        except OSError as exc:
            log.warning("Could not write output.txt: %s", exc)

    def _show_error(self, message: str):
        log.error("Application error: %s", message)
        self._set_status(f"Error: {message}")
        messagebox.showerror("Error", message)
        if self._running:
            self._stop()

    def _set_status(self, message: str):
        self._status_var.set(message)

    def _set_device_chip(self, device: str, compute_type: str):
        self._device_info = (device, compute_type)
        if device == "cuda":
            label = "◆ GPU"
            colour = "#a6e3a1"  # green
        else:
            label = "◆ CPU"
            colour = "#fab387"  # amber
        self._hw_var.set(label)
        self._hw_label.config(fg=colour)

    # ── audio level meter ─────────────────────────────────────────────────────

    def _toggle_meter(self):
        self._meter_visible = not self._meter_visible
        if self._meter_visible:
            self._meter_panel.pack(side=tk.RIGHT, fill=tk.Y)
            self._meter_btn.config(bg=ACCENT, fg=BG)
        else:
            self._meter_panel.pack_forget()
            self._meter_btn.config(bg=ENTRY_BG, fg=FG)

    def _on_level_callback(self, rms: float):
        """Called from AudioCaptureThread — uses after() for thread safety."""
        self.after(0, self._update_meter, rms)

    def _update_meter(self, rms: float):
        self._meter_rms_var.set(f"RMS: {rms:.4f}")
        if not self._meter_visible:
            return
        c = self._meter_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2 or h < 2:
            return

        MAX_RMS = 0.08
        frac = min(rms / MAX_RMS, 1.0)
        thresh = self._threshold_var.get()
        thresh_frac = min(thresh / MAX_RMS, 1.0)
        bar_top = int((1.0 - frac) * h)
        thresh_y = int((1.0 - thresh_frac) * h)

        # Peak hold with slow decay
        if rms > self._meter_peak:
            self._meter_peak = rms
        else:
            self._meter_peak = max(0.0, self._meter_peak - 0.0015)
        peak_y = max(0, int((1.0 - min(self._meter_peak / MAX_RMS, 1.0)) * h))

        # Color: gray = silent, green = speech detected, yellow = loud, red = clipping
        if rms < thresh:
            color = "#45475a"
        elif rms < thresh * 2.5:
            color = "#a6e3a1"
        elif rms < thresh * 6:
            color = "#f9e2af"
        else:
            color = "#f38ba8"

        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#0d0d1a", outline="")
        if bar_top < h:
            c.create_rectangle(6, bar_top, w - 6, h, fill=color, outline="")
        if 0 <= peak_y < h:
            c.create_line(6, peak_y, w - 6, peak_y, fill=color, width=2)
        # Threshold line (blue dashed)
        c.create_line(0, thresh_y, w, thresh_y, fill="#89b4fa", width=2, dash=(5, 3))

    def _on_threshold_change(self, val):
        thresh = float(val)
        self._threshold_display_var.set(f"{thresh:.3f}")
        if self._capture_thread and self._running:
            self._capture_thread.speech_threshold = thresh
        self._save_config()

    def _on_silence_change(self, val):
        ms = int(float(val))
        self._silence_display_var.set(f"{ms} ms")
        if self._capture_thread and self._running:
            self._capture_thread.end_silence_ms = ms
        self._save_config()

    # ── config persistence ────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            with open(self._config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_config(self):
        try:
            data = self._load_config()
            data["threshold"] = round(self._threshold_var.get(), 4)
            data["end_silence_ms"] = self._silence_var.get()
            data["model"] = self._model_var.get()
            data["processor"] = self._processor_var.get()
            data["device"] = self._device_var.get()
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            log.warning("Could not save config: %s", exc)

    # ── shutdown ──────────────────────────────────────────────────────────────

    def _on_close(self):
        self._save_config()
        self._stop()
        self.after(300, self.destroy)
