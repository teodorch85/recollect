"""Windows-specific parts of starting ReCollect: one copy at a time, its own taskbar icon, sharp text on
scaled screens, restarting after an update, and the build's self-test."""
import json
import os
import subprocess
import sys
from pathlib import Path

import config
import updater
from config import APP_DIR, DATA_DIR, ICON_FILE, SETTINGS_FILE


def own_taskbar_entry():
    """Group the window under ReCollect's own icon in the taskbar, not under Python's."""
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ReCollect.PhotoScans")


def dpi_aware():
    """Draw at the screen's real resolution. Without this, Windows stretches the window as a bitmap
    on scaled displays (125%, 150%...), which blurs the text and makes the window too big."""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def work_area():
    """Usable screen area (without the taskbar) in pixels: (left, top, width, height)."""
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def wait_for(pid, seconds=20):
    """After a restart: let the old copy close first, or it would count as 'already running'."""
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(seconds * 1000))
        ctypes.windll.kernel32.CloseHandle(handle)


def relaunch():
    if updater.FROZEN:
        subprocess.Popen([sys.executable, "--after", str(os.getpid())], cwd=Path(sys.executable).parent)
        return
    exe = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen([str(exe if exe.exists() else sys.executable), str(APP_DIR / "photo_scanner.py"),
                      "--after", str(os.getpid())], cwd=APP_DIR)


def first_start_of_exe():
    """ReCollect.exe: bring over settings and calibration from the source version (found through its
    shortcut), and point the shortcuts at the exe. Never stops the app from starting."""
    import shutil
    import install_shortcuts
    exe = str(Path(sys.executable))
    try:
        if not SETTINGS_FILE.exists():
            old = install_shortcuts.old_folder()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            for name in ("settings.json", "empty_bed.png"):
                if old and (old / name).exists():
                    shutil.copy2(old / name, DATA_DIR / name)
        s = config.load_settings()
        if s["shortcuts_for"] != exe:  # first start, or the folder was moved
            install_shortcuts.ensure_for_exe(exe)
            s["shortcuts_for"] = exe
            SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception:
        pass


def selftest():
    """ReCollect.exe --selftest: everything the app loads later is in the bundle (run by the build)."""
    import install_shortcuts  # noqa: F401
    updater.latest_release("0.0.0")  # HTTPS to GitHub works with the bundled certificate check
    import win32con, win32gui, win32event, win32process  # noqa: F401
    from winrt.windows.data.xml.dom import XmlDocument  # noqa: F401
    from winrt.windows.ui.notifications import ToastNotificationManager  # noqa: F401
    from win32com.shell import shell  # noqa: F401
    import win32com.client  # noqa: F401
    import win32cred  # noqa: F401
    import anthropic
    anthropic.Anthropic(api_key="test").beta.messages  # loads the SDK's lazily imported parts
    import sv_ttk  # the theme's own files (sv.tcl and its pictures) must be bundled too
    assert sv_ttk.TCL_THEME_FILE_PATH.exists()
    assert ICON_FILE.exists() and (APP_DIR / "ReCollect.png").exists() and (APP_DIR / "CHANGELOG.md").exists()


def already_running():
    """True (and the open window brought to the front) if ReCollect is already running."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "ReCollect.PhotoScans.single")  # held until this process exits
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    import win32con, win32gui
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h).startswith("ReCollect ") else None, None)
    for h in found:
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(h)
        except Exception:
            pass
    return True
