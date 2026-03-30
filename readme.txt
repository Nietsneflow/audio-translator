================================================
  Audio Translator - README
================================================

WHAT THIS APP DOES
------------------
Listens to audio playing on your computer (or from a microphone)
and translates Russian speech to English in real time, displaying
the translation on screen.


INSTALLATION
------------
1. Download the full folder from Dropbox and save it anywhere
   on your PC (e.g. your Desktop).

   The folder includes the app files AND the speech recognition
   models — no internet download of models is required.

2. Double-click setup.bat

   The installer will:
   - Install Python 3.11 automatically if you don't have it
     (requires Windows 10 version 1709 or later)
   - Download and install all required dependencies
     NOTE: PyTorch is a large download (~2.5 GB). This may take
     several minutes depending on your internet speed.
   - Ask if you want to launch the app immediately when done.

3. After setup is complete, use Launch.bat to start the app
   any time in the future.


MODELS
------
The speech recognition models are included in the models\ folder:

  models\small\    - ~500 MB  (fast, good for CPU)
  models\medium\   - ~1.5 GB  (more accurate, best with GPU)

The app loads them directly from this folder — no internet
connection is needed to start translating.


GPU ACCELERATION (NVIDIA)
-------------------------
If you have an NVIDIA graphics card, the app will automatically
use it for faster translations — no extra steps needed.

Requirements:
- NVIDIA GPU (GTX 1060 or newer recommended)
- NVIDIA drivers version 525 or newer
  Download from: https://www.nvidia.com/drivers

If your drivers are up to date and you have a supported GPU,
the app will say "CUDA" in the device indicator at the top.

If you don't have an NVIDIA GPU, the app will automatically
use your CPU instead. Translation will be slower but still works.

You can also manually choose CPU or GPU using the "Processor"
dropdown in the app's toolbar.


SELECTING AN AUDIO SOURCE
--------------------------
Use the Device dropdown at the top left to select where the
audio comes from:

- To translate audio playing on your PC (YouTube, games, calls):
  Select a "Loopback" or "Stereo Mix" device

- To translate someone speaking into a microphone:
  Select your microphone device

Click Refresh if your device doesn't appear in the list.


MODEL SIZE
----------
Use the Model dropdown to choose translation quality vs speed:

  small   - Fast, good accuracy (recommended for CPU)
  medium  - Better accuracy (recommended for GPU)


USAGE TIPS
----------
- Keep audio volume at a reasonable level. Very quiet audio
  may not be detected.
- The app only processes speech — silence and background noise
  are filtered out automatically.
- Translations appear in the main window as they are processed.
- A rolling log of the last 20 translations is saved to
  output.txt in the app folder.


FILES IN THIS PACKAGE
---------------------
  main.py           - App entry point
  gui.py            - User interface
  transcriber.py    - Speech recognition and translation
  audio_capture.py  - Audio input and voice detection
  logger.py         - Logging system
  requirements.txt  - Python dependency list
  setup.bat         - Installer (run this first)
  Launch.bat        - Start the app after installation
  models\           - Bundled speech recognition models
    small\          - Small model (~500 MB)
    medium\         - Medium model (~1.5 GB)

================================================
