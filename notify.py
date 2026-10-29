"""Tell you a job finished while you're at the scanner: a Windows notification (the usual popup in the
corner, gone after about 5 seconds) and a flashing taskbar button if the window isn't in front.

Windows only shows notifications for an app it knows: the Start menu shortcut made by
install_shortcuts.py registers ReCollect under APP_ID. Without it, a small popup of our own is used.
"""
import ctypes
from ctypes import wintypes
from pathlib import Path
from xml.sax.saxutils import escape

APP_ID = "ReCollect.PhotoScans"  # same as install_shortcuts.APP_ID
LOGO = Path(__file__).resolve().parent / "ReCollect.png"
POPUP_MS = 5000
last_error = None  # why the last Windows notification couldn't be shown (None: it was handed to Windows)


class _FlashInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]


def flash(hwnd):
    """Flash the taskbar button until the window is brought to the front."""
    info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, 0x2 | 0xC, 0, 0)  # FLASHW_TRAY | FLASHW_TIMERNOFG
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def _toast(title, text):
    from winrt.windows.data.xml.dom import XmlDocument
    from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager
    logo = f'<image placement="appLogoOverride" src="{LOGO.as_uri()}"/>' if LOGO.exists() else ""
    xml = XmlDocument()
    xml.load_xml(f'<toast duration="short"><visual><binding template="ToastGeneric"><text>{escape(title)}</text>'
                 f'<text>{escape(text)}</text>{logo}</binding></visual></toast>')
    ToastNotificationManager.create_toast_notifier(APP_ID).show(ToastNotification(xml))


def _popup(root, title, text):
    """Our own corner popup, for when Windows notifications aren't available."""
    import tkinter as tk
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    frame = tk.Frame(win, background="#0F6E56", padx=16, pady=12)
    frame.pack()
    tk.Label(frame, text=title, font=("Segoe UI", 12, "bold"), fg="white", bg="#0F6E56").pack(anchor="w")
    tk.Label(frame, text=text, font=("Segoe UI", 10), fg="white", bg="#0F6E56", wraplength=360,
             justify="left").pack(anchor="w")
    win.update_idletasks()
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)  # work area, above the taskbar
    win.geometry(f"+{r.right - win.winfo_width() - 16}+{r.bottom - win.winfo_height() - 16}")
    win.bind("<Button-1>", lambda e: win.destroy())
    win.after(POPUP_MS, win.destroy)


def job_done(root, title, text, popup=True, flash_taskbar=True):
    """Popup in the corner (about 5 s) and, if the app isn't in front, a flashing taskbar button."""
    global last_error
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()  # Tk's real top-level window
    if flash_taskbar and ctypes.windll.user32.GetForegroundWindow() != hwnd:
        flash(hwnd)
    if not popup:
        return
    text = text if len(text) <= 180 else text[:177] + "…"
    try:
        _toast(title, text)
        last_error = None
    except Exception as e:  # notifications off, or the Start menu shortcut is missing
        last_error = e
        _popup(root, title, text)
