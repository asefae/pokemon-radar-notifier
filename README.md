# Pokémon Radar Notifier (Windows)

A local Pokémon radar notifier for Windows. The app periodically captures selected screen regions, reads them with OCR, compares the recognized text with your target Pokémon names, and sends a Windows notification and/or sound.

The app does not interact with the game, press keys, or read another process's memory.

## Download

Download the latest Windows build from [GitHub Releases](https://github.com/asefae/pokemon-radar-notifier/releases/latest).

Tesseract OCR must be installed separately; it is not included in the executable.

## Requirements

- Windows
- Python 3.10 or later (only if running from source)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
- For Russian Pokémon names, install the Tesseract `rus` language data

## Run from source

In PowerShell, open the project folder and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py main.py
```

## Configure the app

1. Select **Choose radar area**, enter the number and names of the screen regions, then drag over each area in the desktop screenshot. You can add radar areas and a separate central area.
2. Select an area and click **Preview area** to verify the captured image.
3. Enter Pokémon names separated by commas or new lines.
4. Adjust the capture interval (100–1000 ms; default 150 ms), OCR interval (200–2000 ms; default 300 ms), processing mode (`fast`, `balanced`, or `accurate`), OCR/fuzzy-match thresholds, cooldown, and lost-detection confirmation count.
5. Click **Save**, then **Start**.

Settings are stored in `config.json` next to the application. The app checks the standard Tesseract installation paths:

- `C:\Program Files\Tesseract-OCR\tesseract.exe`
- `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`

If needed, you can change the executable path in the configuration file. **Open settings folder** opens the configuration location, and **Export log** saves the history as a TXT file.

## Notifications

Windows toast notifications and sound are available. The optional Telegram integration can be configured in the app:

1. Create a bot with [BotFather](https://t.me/BotFather) and obtain your Telegram chat ID.
2. Enter the bot token and chat ID in the app settings.
3. Enable Telegram notifications and use **Test Telegram**.

Never publish your Telegram bot token or commit it to the repository. Telegram screenshot sending is disabled by default.

For Windows toast notifications, install `winotify`. Monitoring and sound notifications can still work without it. If a required dependency or Tesseract is missing, the app reports the error in its log.

## OCR processing

The current fallback mode uses a sequential OCR pipeline: a daemon thread captures the selected area with `mss` every 1000 ms, saves `debug_raw.png`, enlarges the grayscale image 3×, increases contrast, and runs Tesseract with `--psm 11`. In this mode, the capture/OCR worker queue, fuzzy matching, cooldown, active/lost detection, and Telegram notification pipeline are not used.

**Test OCR now** performs one capture without matching or notifications and displays the raw OCR text in the window/log. Check that the captured image contains readable text before changing OCR settings.

Processing modes:

- `fast`: 3× LANCZOS scaling and two threshold variants.
- `balanced`: adds contrast enhancement and adaptive thresholding.
- `accurate`: uses 4× LANCZOS scaling and all available variants.

Each variant is checked with Tesseract PSM 6 and 11. Matching is performed line by line with conservative corrections for common OCR mistakes, while the original target names are kept in notifications.

For diagnostics, enable `debug_frames` or `pipeline_diagnostics`; processed variants are saved to `debug_directory`. The **Save raw/variants**, **OCR latest frame**, and **OCR saved frame** buttons show the recognized text and traceback. The status area reports capture dimensions and average pixel value, first valid frame readiness, text length, errors, processed frames, and latest-frame queue replacements. Tesseract is checked before monitoring starts; its path and version are recorded in the log.

A Pokémon is notified once when first detected. Continued presence does not trigger repeated alerts. Disappearance is confirmed over multiple scans (default: 2); after a confirmed disappearance, the next appearance can trigger another alert even when cooldown is nonzero.

## Tests

Run the test suite and syntax check:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
```

## Build the executable

To build with PyInstaller:

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name PokemonRadar main.py
```

Or run the included build script:

```powershell
.\build_exe.ps1
```

The executable is created at `dist\PokemonRadar.exe`. It runs as a regular Windows app without a console window. The app creates `config.json` next to the executable. Tesseract must be installed separately.
