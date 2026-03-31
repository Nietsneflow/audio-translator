================================================
  Audio Translator - README
================================================

WHAT THIS APP DOES
------------------
Listens to audio playing on your computer (or a microphone) and
translates speech to English in real time.  It is designed
primarily for Russian audio (games, streams, calls) but
automatically detects any language and translates it to English.

Translated text is shown on screen and saved to text files that
other apps (e.g. OBS, stream overlays) can read.

Optionally, transcribed English text can be re-translated back
to Russian — see "Translate Back to Russian" below.


INSTALLATION (first time)
--------------------------
1. Double-click setup.bat

   The installer will automatically:
   - Install Python 3.11 if you don't have it
     (requires Windows 10 version 1709 or later)
   - Download and install all required Python packages
     NOTE: PyTorch is a large download (~2.5 GB).
     This may take several minutes.
   - Download the offline translation pack for the optional
     English -> Russian back-translation feature (~80 MB)

2. After setup completes, use Launch.bat to start the app.


UPDATING (already installed)
-----------------------------
Double-click Update.bat.  It will:
- Install Git automatically if you don't have it
- Pull the latest version from GitHub
- Update all Python packages
- Re-download the translation pack if needed

If the app is broken or out of sync, use Repair.bat instead —
it force-resets all files to the latest version then updates.


MODELS
------
Speech recognition models are bundled in the models\ folder:

  models\small\    - ~500 MB  (fast, good for CPU)
  models\medium\   - ~1.5 GB  (more accurate, best with GPU)

The app loads them from this folder — no internet connection
is needed once setup is complete.

Use the Model dropdown in the toolbar to switch between them.


GPU ACCELERATION (NVIDIA)
--------------------------
If you have an NVIDIA graphics card the app will automatically
use it for faster processing — no extra steps needed.

Requirements:
  - NVIDIA GPU (GTX 1060 or newer recommended)
  - NVIDIA drivers version 525 or newer
    Download from: https://www.nvidia.com/drivers

If your GPU is supported the status bar will show "CUDA".
Without an NVIDIA GPU the app falls back to CPU automatically —
translation is slower but still works.

You can force CPU or GPU using the Processor dropdown in
the toolbar (Auto / GPU / CPU).


SELECTING AUDIO SOURCES
------------------------
Click the Sources button at the top left.

  Source 1 - primary audio input (required)
  Source 2 - optional second audio input

For each source, choose a device:

  - To translate audio playing on your PC (games, YouTube,
    calls): select a Loopback or Stereo Mix device.

  - To translate a microphone: select your mic device.

Click the refresh button (the circular arrow) if a device
does not appear in the list.

When Source 2 is enabled the transcript window splits into
two side-by-side panes, one per source.  Each source has
its own independent VAD settings (see Level Meter below).


UI CONTROLS
-----------
  Sources    - assign audio devices to Source 1 and Source 2
  [refresh]  - re-scan available audio devices
  Model      - choose small (fast) or medium (accurate)
  Processor  - Auto / GPU / CPU
  Options    - output format toggles (see Options below)
  Start/Stop - begin or end live translation
  Clear      - clear the on-screen transcript panes
  Meter      - show/hide the audio level meter panel


OPTIONS MENU
------------
Transcripts (saves to files)
  Source 1 / Source 2 / Combined — each has:
    Timestamps              - prepend [HH:MM:SS] to each line
    Language tag            - prepend detected language, e.g. [ru]
    Translate Eng -> Rus    - write Russian text instead of English

  Combined output also has:
    Source tag              - prepend {S1} or {S2} to each line

Live View (on-screen panes)
  Same options as Transcripts but only affect what is displayed
  on screen (and printed to the console/PowerShell window).

Always on top
  Keep the app window above all other windows.

All settings are saved automatically and restored on next launch.


OUTPUT FILES
------------
Three text files are written to the app folder while running:

  Source1.txt  - Source 1 transcripts (last 20 entries)
  Source2.txt  - Source 2 transcripts (last 20 entries)
  output.txt   - Combined S1 + S2    (last 40 entries)

Files are overwritten on each run, not appended.
They are suitable for use as OBS text sources or stream overlays.


LEVEL METER & VAD SETTINGS
---------------------------
Click the Meter button to open the audio level panel on the
right side of the window.

  RMS display   - live audio level
  Threshold     - RMS level above which a frame counts as speech.
                  Increase if background noise causes false
                  triggers; decrease if quiet speech is missed.
  Silence gate  - how long silence must persist (milliseconds)
                  before the current utterance is sent for
                  translation.

When Source 2 is active, separate controls appear for each source.
Changes take effect immediately and are saved on close.


TRANSLATE BACK TO RUSSIAN (optional)
--------------------------------------
The app transcribes speech directly to English using Whisper's
built-in translation.  A separate optional feature can
re-translate that English text back into Russian using an
offline AI model (argostranslate).

This is already set up if you ran setup.bat or Update.bat.
To enable it, open Options and check
"Translate English -> Russian" under the desired output.

If the language pack is not installed you will see a prompt
with instructions to run:

    python download_translation_model.py


USAGE TIPS
----------
- Keep audio volume at a reasonable level.  Very quiet audio
  may not be detected.
- The app only processes speech — silence and noise are
  filtered out automatically.
- If translation feels too slow, switch to the small model or
  set Processor to CPU to free up the GPU for other apps.
- Translations are also printed to the PowerShell/console
  window in the format:  [S1] [HH:MM:SS] text


FILES IN THIS PACKAGE
---------------------
  main.py                        - App entry point
  gui.py                         - User interface
  transcriber.py                 - Speech recognition engine
  audio_capture.py               - Audio capture and VAD
  logger.py                      - Logging system
  requirements.txt               - Python dependency list
  download_translation_model.py  - Downloads en->ru language pack
  setup.bat                      - First-time installer
  Update.bat                     - Pull latest version and update
  Repair.bat                     - Force-reset broken installation
  Launch.bat                     - Start the app
  models\small\                  - Bundled small Whisper model
  models\medium\                 - Bundled medium Whisper model

================================================
