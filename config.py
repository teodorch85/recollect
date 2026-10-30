"""Where ReCollect keeps its own files, and its settings."""
import json
import os
import re
from pathlib import Path

import naming
import updater

APP_DIR = Path(__file__).resolve().parent  # the app's own files (inside ReCollect.exe: its _internal folder)
# Settings and calibration: next to the code when run from source; for ReCollect.exe in the user's
# AppData, so an update can replace the program folder without losing them.
DATA_DIR = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReCollect") if updater.FROZEN else APP_DIR
SETTINGS_FILE = DATA_DIR / "settings.json"
REFERENCE_FILE = DATA_DIR / "empty_bed.png"
ICON_FILE = APP_DIR / "ReCollect.ico"

DEFAULTS = {
    "output": str(Path.home() / "Pictures" / "PhotoScans"),
    "front_dpi": 600,
    "back_dpi": 300,
    "format": "JPEG",
    "jpeg_quality": 100,
    "sharpen": "High",
    "restore_color": True,
    "scan_backs": True,
    "batch": "",
    "notify": True,         # popup when a job finishes
    "flash": True,          # flash the taskbar button when the window isn't in front
    "check_updates": True,
    "auto_rotate": False,   # ask Claude which way is up (needs an API key)
    "name_template": naming.DEFAULT,  # folders and file names, Immich storage-template style
    "shortcuts_for": "",    # the ReCollect.exe the Desktop and Start menu shortcuts point to
}


def load_settings():
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_settings(s):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass


def disk_version():
    """The version now in this folder (a release replaces the files while the app keeps running)."""
    try:
        m = re.search(r'__version__\s*=\s*"([\d.]+)"', (APP_DIR / "version.py").read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def newer(a, b):
    try:
        return tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))
    except ValueError:  # version.py caught half-written during an update
        return False
