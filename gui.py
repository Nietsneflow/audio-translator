"""
gui.py
tkinter GUI for the live audio translator.
Provides: Sources cascade menu, model/processor selectors, start/stop,
options (timestamps, language tag, always-on-top), side-by-side dual
transcript panes, independent per-source audio level meters, and
persistent output to Source1.txt / Source2.txt / output.txt.
"""

import collections
import json
import os
import queue
import threading
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
        self._capture_thread_s2: AudioCaptureThread | None = None
        self._transcriber_thread: TranscriberThread | None = None
        self._running = False
        self._poll_id: str | None = None  # after() handle for queue-depth polling
        self._stats_stop = threading.Event()  # signals the stats thread to exit
        self._output_lines: collections.deque = collections.deque(maxlen=20)
        self._s2_output_lines: collections.deque = collections.deque(maxlen=20)
        self._combined_output_lines: collections.deque = collections.deque(maxlen=40)
        self._output_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "Source1.txt"
        )
        self._s2_output_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "Source2.txt"
        )
        self._combined_output_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output.txt"
        )
        self._config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json"
        )

        _cfg = self._load_config()

        saved_threshold = _cfg.get("threshold", SPEECH_THRESHOLD)
        self._threshold_var = tk.DoubleVar(value=saved_threshold)
        self._threshold_display_var = tk.StringVar(value=f"{saved_threshold:.3f}")
        saved_silence = int(_cfg.get("end_silence_ms", END_SILENCE_MS))
        self._silence_var = tk.IntVar(value=saved_silence)
        self._silence_display_var = tk.StringVar(value=f"{saved_silence} ms")

        s2_thresh = _cfg.get("s2_threshold", SPEECH_THRESHOLD)
        self._s2_threshold_var = tk.DoubleVar(value=s2_thresh)
        self._s2_threshold_display_var = tk.StringVar(value=f"{s2_thresh:.3f}")
        s2_sil = int(_cfg.get("s2_end_silence_ms", END_SILENCE_MS))
        self._s2_silence_var = tk.IntVar(value=s2_sil)
        self._s2_silence_display_var = tk.StringVar(value=f"{s2_sil} ms")

        self._meter_visible = False
        self._meter_peak = 0.0
        self._s2_meter_peak = 0.0

        self._s1_device = _cfg.get("s1_device", _cfg.get("device", None))
        self._s2_device = _cfg.get("s2_device", None)  # None = disabled

        # ── Per-file output options ───────────────────────────────────────────
        self._file_s1_ts_var   = tk.BooleanVar(value=_cfg.get("file_s1_ts",        True))
        self._file_s1_lang_var = tk.BooleanVar(value=_cfg.get("file_s1_lang",       False))
        self._file_s2_ts_var   = tk.BooleanVar(value=_cfg.get("file_s2_ts",        True))
        self._file_s2_lang_var = tk.BooleanVar(value=_cfg.get("file_s2_lang",       False))
        self._file_com_ts_var  = tk.BooleanVar(value=_cfg.get("file_combined_ts",   True))
        self._file_com_lang_var= tk.BooleanVar(value=_cfg.get("file_combined_lang",  False))
        self._file_com_src_var = tk.BooleanVar(value=_cfg.get("file_combined_src",  True))

        # ── Live-view (on-screen transcript panes) options ────────────────────
        self._view_ts_var      = tk.BooleanVar(value=_cfg.get("view_ts",            True))
        self._view_lang_var    = tk.BooleanVar(value=_cfg.get("view_lang",           False))

        self._model_var = tk.StringVar(value=_cfg.get("model", DEFAULT_MODEL_LABEL))
        self._processor_var = tk.StringVar(value=_cfg.get("processor", "Auto"))

        self._build_ui()
        self._refresh_devices()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── top controls bar ──────────────────────────────────────────────────
        ctrl = tk.Frame(self, bg=BG, pady=8, padx=10)
        ctrl.pack(fill=tk.X)

        # Column 3 (model combo) absorbs spare width.
        ctrl.columnconfigure(3, weight=1)

        # Sources dropdown button
        self._sources_btn = tk.Menubutton(
            ctrl, text="Sources ▾", bg=ENTRY_BG, fg=FG, relief=tk.FLAT,
            font=("Segoe UI", 10), padx=10, pady=4, cursor="hand2",
            activebackground="#3e3e5e", activeforeground=FG
        )
        self._sources_menu = tk.Menu(
            self._sources_btn, tearoff=0, bg=ENTRY_BG, fg=FG,
            activebackground="#3e3e5e", activeforeground=FG,
            selectcolor=FG
        )
        self._sources_btn["menu"] = self._sources_menu
        self._sources_btn.grid(row=0, column=0, padx=(0, 12), sticky=tk.W)

        refresh_btn = tk.Button(
            ctrl, text="↺", bg=ENTRY_BG, fg=ACCENT, relief=tk.FLAT,
            command=self._refresh_devices, cursor="hand2"
        )
        refresh_btn.grid(row=0, column=1, padx=(0, 12))

        # Model selector
        tk.Label(ctrl, text="Model:", bg=BG, fg=FG).grid(
            row=0, column=2, sticky=tk.W
        )
        self._model_combo = ttk.Combobox(
            ctrl, textvariable=self._model_var,
            values=list(MODEL_OPTIONS.keys()), state="readonly"
        )
        self._model_combo.grid(row=0, column=3, padx=(4, 12), sticky=tk.EW)
        self._model_combo.bind("<<ComboboxSelected>>", lambda _: self._save_config())

        # Processor selector
        tk.Label(ctrl, text="Processor:", bg=BG, fg=FG).grid(
            row=0, column=4, sticky=tk.W
        )
        self._proc_combo = ttk.Combobox(
            ctrl, textvariable=self._processor_var,
            values=["Auto", "GPU", "CPU"], state="readonly", width=6
        )
        self._proc_combo.grid(row=0, column=5, padx=(4, 12), sticky=tk.W)
        self._proc_combo.bind("<<ComboboxSelected>>", lambda _: self._save_config())

        # Options dropdown
        self._ontop_var = tk.BooleanVar(value=False)
        self._options_btn = tk.Menubutton(
            ctrl, text="Options ▾", bg=ENTRY_BG, fg=FG, relief=tk.FLAT,
            font=("Segoe UI", 10), padx=10, pady=4, cursor="hand2",
            activebackground="#3e3e5e", activeforeground=FG
        )
        self._options_menu = tk.Menu(
            self._options_btn, tearoff=0, bg=ENTRY_BG, fg=FG,
            activebackground="#3e3e5e", activeforeground=FG,
            selectcolor=FG
        )
        self._options_btn["menu"] = self._options_menu
        self._options_btn.grid(row=0, column=6, padx=(0, 12), sticky=tk.W)
        self._rebuild_options_menu()

        # Start / Stop button
        self._toggle_btn = tk.Button(
            ctrl, text="▶  Start", bg=BTN_START, fg="#1e1e2e",
            font=("Segoe UI", 10, "bold"), relief=tk.FLAT,
            padx=14, pady=4, cursor="hand2", command=self._toggle
        )
        self._toggle_btn.grid(row=0, column=7, padx=4, sticky=tk.E)

        # Clear button
        clear_btn = tk.Button(
            ctrl, text="Clear", bg=ENTRY_BG, fg=FG, relief=tk.FLAT,
            padx=10, pady=4, cursor="hand2", command=self._clear_text
        )
        clear_btn.grid(row=0, column=8, padx=(0, 4), sticky=tk.E)

        # Meter toggle button
        self._meter_btn = tk.Button(
            ctrl, text="◎ Meter", bg=ENTRY_BG, fg=FG, relief=tk.FLAT,
            padx=8, pady=4, cursor="hand2", command=self._toggle_meter
        )
        self._meter_btn.grid(row=0, column=9, padx=(0, 4), sticky=tk.E)

        # ── content area: transcript pane(s) + optional meter panel ─────────
        self._content_frame = tk.Frame(self, bg=BG)
        self._content_frame.pack(fill=tk.BOTH, expand=True)

        # Transcript container — rebuilt by _rebuild_transcript_panes()
        self._transcript_container = tk.Frame(self._content_frame, bg=BG)
        self._transcript_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._text = None       # S1 ScrolledText (always present when running)
        self._text_s2 = None    # S2 ScrolledText (present only when S2 active)
        self._rebuild_transcript_panes()

        # ── audio level meter panel (right side, toggled) ─────────────────────
        # Outer container — width adjusts via _rebuild_meter_panel()
        self._meter_panel = tk.Frame(self._content_frame, bg="#13131e")
        self._meter_panel.pack_propagate(False)
        # not packed yet — shown/hidden by _toggle_meter() / _rebuild_meter_panel()
        self._rebuild_meter_panel()

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

        self._start_stats_thread()  # start immediately

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

    def _make_text_pane(self, parent: tk.Frame, label: str) -> scrolledtext.ScrolledText:
        """Create a labeled transcript pane inside *parent*. Returns the ScrolledText."""
        tk.Label(parent, text=label, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 9, "bold"), pady=3,
                 width=1).pack(fill=tk.X, padx=8)
        st = scrolledtext.ScrolledText(
            parent, bg=TEXT_BG, fg=FG, font=("Segoe UI", 13),
            wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT,
            padx=12, pady=10, insertbackground=FG, width=1
        )
        st.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        st.tag_configure("ts", foreground=ACCENT, font=("Segoe UI", 10))
        st.tag_configure("lang", foreground="#f9e2af", font=("Segoe UI", 10))
        st.tag_configure("txt", foreground=FG, font=("Segoe UI", 13))
        tk.Button(
            parent, text="✕ clear", bg="#2a2a3e", fg="#585b70",
            font=("Segoe UI", 8), relief=tk.FLAT, padx=6, pady=2,
            cursor="hand2", command=lambda w=st: self._clear_pane(w),
            activebackground="#313244", activeforeground=FG,
        ).place(in_=st, relx=1.0, rely=1.0, x=-18, y=-8, anchor="se")
        return st

    def _clear_pane(self, widget: scrolledtext.ScrolledText):
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.config(state=tk.DISABLED)

    def _rebuild_transcript_panes(self):
        """Destroy and recreate transcript pane(s) based on whether S2 is active."""
        for child in self._transcript_container.winfo_children():
            child.destroy()
        # Reset any previous grid config
        for col in range(self._transcript_container.grid_size()[0] or 3):
            self._transcript_container.columnconfigure(col, weight=0, uniform="")
        self._text = None
        self._text_s2 = None

        s2_active = bool(self._s2_device)
        s1_name = self._s1_device or "Source 1"
        s2_name = self._s2_device or "Source 2"

        self._transcript_container.rowconfigure(0, weight=1)
        if s2_active:
            # Equal-width columns enforced by uniform group
            self._transcript_container.columnconfigure(0, weight=1, uniform="pane")
            self._transcript_container.columnconfigure(1, weight=0, uniform="")
            self._transcript_container.columnconfigure(2, weight=1, uniform="pane")
            f1 = tk.Frame(self._transcript_container, bg=BG)
            f1.grid(row=0, column=0, sticky="nsew")
            tk.Frame(self._transcript_container, bg="#45475a", width=2).grid(
                row=0, column=1, sticky="ns", pady=4)
            f2 = tk.Frame(self._transcript_container, bg=BG)
            f2.grid(row=0, column=2, sticky="nsew")
            self._text = self._make_text_pane(f1, f"Source 1 — {s1_name}")
            self._text_s2 = self._make_text_pane(f2, f"Source 2 — {s2_name}")
        else:
            self._transcript_container.columnconfigure(0, weight=1, uniform="")
            f1 = tk.Frame(self._transcript_container, bg=BG)
            f1.grid(row=0, column=0, sticky="nsew")
            self._text = self._make_text_pane(f1, f"Source 1 — {s1_name}")

    def _build_single_meter_col(self, parent: tk.Frame,
                                 thresh_var: tk.DoubleVar,
                                 thresh_disp: tk.StringVar,
                                 silence_var: tk.IntVar,
                                 silence_disp: tk.StringVar,
                                 rms_var: tk.StringVar,
                                 canvas_attr: str,
                                 thresh_cmd,
                                 silence_cmd,
                                 label: str):
        """Build one meter column inside *parent*. Stores canvas in self.<canvas_attr>."""
        tk.Label(parent, text=label, bg="#13131e", fg=ACCENT,
                 font=("Segoe UI", 8, "bold"), pady=4).pack()
        canvas = tk.Canvas(
            parent, bg="#0d0d1a", highlightthickness=0, width=90, height=180
        )
        canvas.pack(padx=4, pady=(0, 4))
        setattr(self, canvas_attr, canvas)

        tk.Label(parent, textvariable=rms_var, bg="#13131e", fg=FG,
                 font=("Segoe UI", 8)).pack()
        tk.Frame(parent, bg="#313244", height=1).pack(fill=tk.X, padx=4, pady=4)
        tk.Label(parent, text="SENSITIVITY", bg="#13131e", fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).pack()
        tk.Label(parent, text="▲ more", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Scale(parent, variable=thresh_var, from_=0.001, to=0.080,
                 resolution=0.001, orient=tk.VERTICAL, bg="#13131e", fg=FG,
                 troughcolor=ENTRY_BG, highlightthickness=0,
                 command=thresh_cmd, length=90, showvalue=False).pack(pady=2)
        tk.Label(parent, text="▼ less", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Label(parent, textvariable=thresh_disp, bg="#13131e", fg="#89b4fa",
                 font=("Segoe UI", 9, "bold")).pack()
        tk.Label(parent, text="threshold", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Frame(parent, bg="#313244", height=1).pack(fill=tk.X, padx=4, pady=4)
        tk.Label(parent, text="PAUSE GATE", bg="#13131e", fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).pack()
        tk.Label(parent, text="longer ▲", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Scale(parent, variable=silence_var, from_=600, to=50,
                 resolution=50, orient=tk.VERTICAL, bg="#13131e", fg=FG,
                 troughcolor=ENTRY_BG, highlightthickness=0,
                 command=silence_cmd, length=70, showvalue=False).pack(pady=2)
        tk.Label(parent, text="shorter ▼", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()
        tk.Label(parent, textvariable=silence_disp, bg="#13131e", fg="#89b4fa",
                 font=("Segoe UI", 9, "bold")).pack()
        tk.Label(parent, text="end-silence", bg="#13131e", fg="#6c7086",
                 font=("Segoe UI", 7)).pack()

    def _rebuild_meter_panel(self):
        """Destroy and recreate meter column(s) based on whether S2 is active."""
        for child in self._meter_panel.winfo_children():
            child.destroy()
        self._meter_canvas = None
        self._meter_canvas_s2 = None
        self._meter_rms_var = tk.StringVar(value="RMS: 0.000")
        self._meter_rms_s2_var = tk.StringVar(value="RMS: 0.000")

        s2_active = bool(self._s2_device)
        panel_width = 250 if s2_active else 130
        self._meter_panel.config(width=panel_width)

        if s2_active:
            col1 = tk.Frame(self._meter_panel, bg="#13131e")
            col1.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0))
            tk.Frame(self._meter_panel, bg="#313244", width=1).pack(
                side=tk.LEFT, fill=tk.Y, pady=4)
            col2 = tk.Frame(self._meter_panel, bg="#13131e")
            col2.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
            self._build_single_meter_col(
                col1, self._threshold_var, self._threshold_display_var,
                self._silence_var, self._silence_display_var,
                self._meter_rms_var, "_meter_canvas",
                self._on_threshold_change, self._on_silence_change, "S1 LEVEL"
            )
            self._build_single_meter_col(
                col2, self._s2_threshold_var, self._s2_threshold_display_var,
                self._s2_silence_var, self._s2_silence_display_var,
                self._meter_rms_s2_var, "_meter_canvas_s2",
                self._on_s2_threshold_change, self._on_s2_silence_change, "S2 LEVEL"
            )
        else:
            col1 = tk.Frame(self._meter_panel, bg="#13131e")
            col1.pack(fill=tk.BOTH, expand=True)
            self._build_single_meter_col(
                col1, self._threshold_var, self._threshold_display_var,
                self._silence_var, self._silence_display_var,
                self._meter_rms_var, "_meter_canvas",
                self._on_threshold_change, self._on_silence_change, "LEVEL METER"
            )

    def _refresh_devices(self):
        devices = list_loopback_devices()
        self._all_devices = devices if devices else []

        # Determine S1 default if none saved
        if not self._s1_device and devices:
            default_name = get_default_output_name()
            if default_name:
                for name in devices:
                    if default_name.lower() in name.lower() or name.lower() in default_name.lower():
                        self._s1_device = name
                        break
            if not self._s1_device:
                self._s1_device = devices[0]

        self._rebuild_sources_menu()

    def _rebuild_options_menu(self):
        """Rebuild the Options dropdown.

        Options ▾
          Transcripts ►         (output files)
            Source 1 ►
              ✓ Timestamps
              ✓ Language tag  ([ru])
            Source 2 ►          (only when S2 active)
              ✓ Timestamps
              ✓ Language tag  ([ru])
            Combined ►
              ✓ Timestamps
              ✓ Language tag  ([ru])
              ✓ Source tag    ({S1}, {S2})
          Live View ►           (on-screen panes — applies to both S1 and S2)
            ✓ Timestamps
            ✓ Language tag  ([ru])
          ─────────────────────
          Always on top
        """
        menu = self._options_menu
        menu.delete(0, "end")

        sub_kw = dict(tearoff=0, bg=ENTRY_BG, fg=FG,
                      activebackground="#3e3e5e", activeforeground=FG,
                      selectcolor=FG)

        # ── Transcripts cascade ───────────────────────────────────────────────
        transcripts_menu = tk.Menu(menu, **sub_kw)

        # Source 1 sub-cascade
        sub_s1 = tk.Menu(transcripts_menu, **sub_kw)
        sub_s1.add_checkbutton(label="Timestamps",          variable=self._file_s1_ts_var,   command=self._save_repost)
        sub_s1.add_checkbutton(label="Language tag ([ru])", variable=self._file_s1_lang_var, command=self._save_repost)
        transcripts_menu.add_cascade(label="Source 1", menu=sub_s1)

        # Source 2 sub-cascade (only when S2 is active)
        if self._s2_device:
            sub_s2 = tk.Menu(transcripts_menu, **sub_kw)
            sub_s2.add_checkbutton(label="Timestamps",          variable=self._file_s2_ts_var,   command=self._save_repost)
            sub_s2.add_checkbutton(label="Language tag ([ru])", variable=self._file_s2_lang_var, command=self._save_repost)
            transcripts_menu.add_cascade(label="Source 2", menu=sub_s2)

        # Combined sub-cascade
        sub_com = tk.Menu(transcripts_menu, **sub_kw)
        sub_com.add_checkbutton(label="Timestamps",              variable=self._file_com_ts_var,  command=self._save_repost)
        sub_com.add_checkbutton(label="Language tag ([ru])",     variable=self._file_com_lang_var,command=self._save_repost)
        sub_com.add_checkbutton(label="Source tag ({S1}, {S2})", variable=self._file_com_src_var, command=self._save_repost)
        transcripts_menu.add_cascade(label="Combined", menu=sub_com)

        menu.add_cascade(label="Transcripts \u25ba", menu=transcripts_menu)

        # ── Live View cascade ─────────────────────────────────────────────────
        live_menu = tk.Menu(menu, **sub_kw)
        live_menu.add_checkbutton(label="Timestamps",          variable=self._view_ts_var,   command=self._save_repost)
        live_menu.add_checkbutton(label="Language tag ([ru])", variable=self._view_lang_var, command=self._save_repost)
        menu.add_cascade(label="Live View \u25ba", menu=live_menu)

        menu.add_separator()
        menu.add_checkbutton(label="Always on top", variable=self._ontop_var, command=self._toggle_ontop)

    def _rebuild_sources_menu(self):
        """Rebuild the Sources dropdown as a two-entry cascade tree.

        Sources ▾
          Source 1 ►  device A
                      ✓ device B   ← currently selected
                      device C
          Source 2 ►  (disabled)   ← toggle off
                      device A
                      ✓ device B   ← currently selected
                      device C  (Already Source 1, grayed)
        """
        menu = self._sources_menu
        menu.delete(0, "end")
        devices = getattr(self, "_all_devices", [])

        menu_kw = dict(tearoff=0, bg=ENTRY_BG, fg=FG,
                       activebackground="#3e3e5e", activeforeground=FG)

        # ── Source 1 cascade ─────────────────────────────────────────────────
        sub1 = tk.Menu(menu, **menu_kw)
        for name in devices:
            if name == self._s2_device:
                sub1.add_command(
                    label=f"  {name}  (Already S2)",
                    foreground="#6c7086",
                    command=lambda n=name: self._select_source(1, n),
                )
            else:
                sub1.add_command(
                    label=f"✓ {name}" if name == self._s1_device else f"  {name}",
                    command=lambda n=name: self._select_source(1, n),
                )
        s1_label = f"Source 1  [{self._s1_device or 'none'}]" if self._s1_device else "Source 1  [none]"
        menu.add_cascade(label=s1_label, menu=sub1)

        # ── Source 2 cascade ─────────────────────────────────────────────────
        sub2 = tk.Menu(menu, **menu_kw)
        sub2.add_command(
            label="✓ (disabled)" if not self._s2_device else "  (disabled)",
            command=lambda: self._select_source(2, None),
        )
        if devices:
            sub2.add_separator()
        for name in devices:
            if name == self._s1_device:
                sub2.add_command(
                    label=f"  {name}  (Already S1)",
                    foreground="#6c7086",
                    command=lambda n=name: self._select_source(2, n),
                )
            else:
                sub2.add_command(
                    label=f"✓ {name}" if name == self._s2_device else f"  {name}",
                    command=lambda n=name: self._select_source(2, n),
                )
        s2_label = f"Source 2  [{self._s2_device}]" if self._s2_device else "Source 2  [disabled]"
        menu.add_cascade(label=s2_label, menu=sub2)

    def _select_source(self, source_num: int, device_name: str | None):
        """Called when user picks a device for Source 1 or 2."""
        if source_num == 1:
            if device_name and device_name == self._s1_device:
                return  # S1 can't be disabled
            self._s1_device = device_name
        else:
            # Toggle off if already selected
            if device_name == self._s2_device:
                self._s2_device = None
            else:
                self._s2_device = device_name

        self._save_config()
        self._rebuild_sources_menu()
        self._rebuild_options_menu()
        self._rebuild_transcript_panes()
        self._rebuild_meter_panel()

        if self._running:
            self._restart_capture_all()

    def _restart_capture_all(self):
        """Stop all capture threads and restart with current S1/S2 settings."""
        if not self._s1_device:
            return
        log.info("Restarting capture — S1=%r S2=%r", self._s1_device, self._s2_device)
        self._set_status("Switching sources…")

        if self._capture_thread:
            self._capture_thread.stop()
        if self._capture_thread_s2:
            self._capture_thread_s2.stop()

        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._capture_thread = AudioCaptureThread(
            audio_queue=self._audio_queue,
            device_name=self._s1_device,
            speech_threshold=self._threshold_var.get(),
            end_silence_ms=self._silence_var.get(),
            on_level=self._on_level_callback,
            source_id=1,
        )
        self._capture_thread.start()
        self._meter_peak = 0.0

        if self._s2_device:
            self._capture_thread_s2 = AudioCaptureThread(
                audio_queue=self._audio_queue,
                device_name=self._s2_device,
                speech_threshold=self._s2_threshold_var.get(),
                end_silence_ms=self._s2_silence_var.get(),
                on_level=self._on_s2_level_callback,
                source_id=2,
            )
            self._capture_thread_s2.start()
            self._s2_meter_peak = 0.0
        else:
            self._capture_thread_s2 = None

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

        device_name = self._s1_device
        open_session_log()
        log.info("Start requested — S1=%r S2=%r model=%r", device_name, self._s2_device, self._model_var.get())
        if not device_name:
            log.error("No S1 device selected — cannot start")
            messagebox.showerror(
                "No device",
                "No Source 1 audio device selected.\n\n"
                "Click Sources \u25be to select an audio device."
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
            source_id=1,
        )
        self._capture_thread_s2 = None
        if self._s2_device:
            self._capture_thread_s2 = AudioCaptureThread(
                audio_queue=self._audio_queue,
                device_name=self._s2_device,
                speech_threshold=self._s2_threshold_var.get(),
                end_silence_ms=self._s2_silence_var.get(),
                on_level=self._on_s2_level_callback,
                source_id=2,
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
        if self._capture_thread_s2:
            self._capture_thread_s2.start()
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
        if self._capture_thread_s2:
            self._capture_thread_s2.stop()
        self._capture_thread_s2 = None
        self._running = False
        self._queue_var.set("")
        self._hw_var.set("")
        self._hw_label.config(bg=ENTRY_BG)
        self._toggle_btn.config(text="▶  Start", bg=BTN_START)
        self._model_combo.config(state="readonly")
        self._proc_combo.config(state="readonly")
        self._meter_peak = 0.0
        self._s2_meter_peak = 0.0
        self._meter_rms_var.set("RMS: 0.000")
        self._meter_rms_s2_var.set("RMS: 0.000")
        close_session_log()

    def _clear_text(self):
        self._clear_pane(self._text)
        if self._text_s2:
            self._clear_pane(self._text_s2)

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

    def _start_stats_thread(self):
        """Start a single persistent daemon thread that polls stats every 2 s."""
        t = threading.Thread(target=self._stats_loop, daemon=True, name="StatsThread")
        t.start()

    def _stats_loop(self):
        """Persistent stats thread — polls every 2 s until window closes."""
        while not self._stats_stop.wait(timeout=2.0):
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

            try:
                self.after(0, self._stats_cpu_var.set, cpu_str)
                self.after(0, self._stats_ram_var.set, ram_str)
                self.after(0, self._stats_gpu_var.set, gpu_str)
                self.after(0, self._stats_vram_var.set, vram_str)
            except Exception:
                break  # window destroyed

    # ── thread-safe callbacks (called from background threads) ────────────────

    def _on_result(self, timestamp: str, text: str, language: str, source_id: int = 1):
        """Called from TranscriberThread — must post to main thread via after()."""
        self.after(0, self._append_result, timestamp, text, language, source_id)

    def _on_error(self, message: str):
        self.after(0, self._show_error, message)

    def _on_status(self, message: str):
        self.after(0, self._set_status, message)

    def _on_device_info(self, device: str, compute_type: str):
        self.after(0, self._set_device_chip, device, compute_type)

    # ── main-thread UI updates ────────────────────────────────────────────────

    def _append_result(self, timestamp: str, text: str, language: str, source_id: int = 1):
        widget = self._text if (source_id == 1 or self._text_s2 is None) else self._text_s2
        widget.config(state=tk.NORMAL)
        if self._view_ts_var.get():
            widget.insert(tk.END, f"[{timestamp}] ", "ts")
        if self._view_lang_var.get():
            widget.insert(tk.END, f"[{language}] ", "lang")
        widget.insert(tk.END, f"{text}\n", "txt")
        widget.see(tk.END)
        widget.config(state=tk.DISABLED)

        # ── Source 1 / Source 2 individual files ─────────────────────────────
        if source_id == 1:
            ts_flag, lang_flag = self._file_s1_ts_var.get(), self._file_s1_lang_var.get()
        else:
            ts_flag, lang_flag = self._file_s2_ts_var.get(), self._file_s2_lang_var.get()
        lang_prefix = f"[{language}] " if lang_flag else ""
        line = f"{lang_prefix}{text}"
        ts_line = f"[{timestamp}] {line}" if ts_flag else line

        if source_id == 1:
            self._output_lines.append(ts_line)
            self._write_source_file(self._output_file, self._output_lines)
        else:
            self._s2_output_lines.append(ts_line)
            self._write_source_file(self._s2_output_file, self._s2_output_lines)

        # ── Combined file ─────────────────────────────────────────────────────
        com_lang_prefix = f"[{language}] " if self._file_com_lang_var.get() else ""
        com_line = f"{com_lang_prefix}{text}"
        com_ts_line = f"[{timestamp}] {com_line}" if self._file_com_ts_var.get() else com_line
        src_tag = "{S1}" if source_id == 1 else "{S2}"
        combined_line = f"{src_tag} {com_ts_line}" if self._file_com_src_var.get() else com_ts_line
        self._combined_output_lines.append(combined_line)
        self._write_source_file(self._combined_output_file, self._combined_output_lines)

    def _write_source_file(self, path: str, lines):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError as exc:
            log.warning("Could not write %s: %s", path, exc)

    def _show_error(self, message: str):
        log.error("Application error: %s", message)
        self._set_status(f"Error: {message}")
        messagebox.showerror("Error", message)
        if self._running:
            self._stop()

    def _set_status(self, message: str):
        self._status_var.set(message)

    def _set_device_chip(self, device: str, compute_type: str):
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
        """Called from AudioCaptureThread S1 — uses after() for thread safety."""
        self.after(0, self._update_meter, rms)

    def _on_s2_level_callback(self, rms: float):
        """Called from AudioCaptureThread S2 — uses after() for thread safety."""
        self.after(0, self._update_meter_s2, rms)

    def _on_s2_threshold_change(self, val):
        self._s2_threshold_display_var.set(f"{float(val):.3f}")
        if self._capture_thread_s2 and self._running:
            self._capture_thread_s2.speech_threshold = float(val)
        self._save_config()

    def _on_s2_silence_change(self, val):
        ms = int(float(val))
        self._s2_silence_display_var.set(f"{ms} ms")
        if self._capture_thread_s2 and self._running:
            self._capture_thread_s2.end_silence_ms = ms
        self._save_config()

    def _update_meter_s2(self, rms: float):
        if not hasattr(self, "_meter_rms_s2_var"):
            return
        self._meter_rms_s2_var.set(f"RMS: {rms:.4f}")
        if not self._meter_visible:
            return
        c = getattr(self, "_meter_canvas_s2", None)
        if c is None:
            return
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2 or h < 2:
            return
        MAX_RMS = 0.08
        frac = min(rms / MAX_RMS, 1.0)
        thresh = self._s2_threshold_var.get()
        thresh_frac = min(thresh / MAX_RMS, 1.0)
        bar_top = int((1.0 - frac) * h)
        thresh_y = int((1.0 - thresh_frac) * h)
        if rms > self._s2_meter_peak:
            self._s2_meter_peak = rms
        else:
            self._s2_meter_peak = max(0.0, self._s2_meter_peak - 0.0015)
        peak_y = max(0, int((1.0 - min(self._s2_meter_peak / MAX_RMS, 1.0)) * h))
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
        c.create_line(0, thresh_y, w, thresh_y, fill="#89b4fa", width=2, dash=(5, 3))

    def _update_meter(self, rms: float):
        self._meter_rms_var.set(f"RMS: {rms:.4f}")
        if not self._meter_visible:
            return
        c = getattr(self, "_meter_canvas", None)
        if c is None:
            return
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

    def _save_repost(self):
        """Save config then re-post the Options menu so it stays visible."""
        self._save_config()
        x = self._options_btn.winfo_rootx()
        y = self._options_btn.winfo_rooty() + self._options_btn.winfo_height()
        self.after(1, lambda: self._options_menu.post(x, y))

    def _save_config(self):
        try:
            data = self._load_config()
            data["threshold"] = round(self._threshold_var.get(), 4)
            data["end_silence_ms"] = self._silence_var.get()
            data["model"] = self._model_var.get()
            data["processor"] = self._processor_var.get()
            data["s1_device"] = self._s1_device
            data["s2_device"] = self._s2_device
            data["s2_threshold"] = round(self._s2_threshold_var.get(), 4)
            data["s2_end_silence_ms"] = self._s2_silence_var.get()
            data["file_s1_ts"]       = self._file_s1_ts_var.get()
            data["file_s1_lang"]     = self._file_s1_lang_var.get()
            data["file_s2_ts"]       = self._file_s2_ts_var.get()
            data["file_s2_lang"]     = self._file_s2_lang_var.get()
            data["file_combined_ts"] = self._file_com_ts_var.get()
            data["file_combined_lang"]= self._file_com_lang_var.get()
            data["file_combined_src"]= self._file_com_src_var.get()
            data["view_ts"]          = self._view_ts_var.get()
            data["view_lang"]        = self._view_lang_var.get()
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            log.warning("Could not save config: %s", exc)

    # ── shutdown ──────────────────────────────────────────────────────────────

    def _on_close(self):
        self._save_config()
        self._stats_stop.set()  # signal stats thread to exit cleanly
        self._stop()
        self.after(300, self.destroy)
