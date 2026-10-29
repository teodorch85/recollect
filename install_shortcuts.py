"""Create ReCollect shortcuts on the Desktop, in the Start menu and in this folder (run again if the
folder moves). ReCollect.exe makes its own on first start (see ensure_for_exe).

They carry the app's icon and taskbar ID, so pinning one to the taskbar and the running window
share a single taskbar button, and Windows shows ReCollect's notifications.
"""
import sys
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell, shellcon

APP_DIR = Path(__file__).resolve().parent
APP_ID = "ReCollect.PhotoScans"  # same as photo_scanner._own_taskbar_entry


def _folder(csidl):
    return Path(shell.SHGetFolderPath(0, csidl, None, 0))


def make(path, target=None, args="photo_scanner.py", icon=None):
    """A shortcut at `path`; by default to this folder's Python, running photo_scanner.py."""
    target = Path(target or APP_DIR / ".venv" / "Scripts" / "pythonw.exe")
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                      shell.IID_IShellLink)
    link.SetPath(str(target))
    link.SetArguments(args)
    link.SetWorkingDirectory(str(target.parent if args == "" else APP_DIR))
    link.SetIconLocation(str(icon or APP_DIR / "ReCollect.ico"), 0)
    link.SetDescription("Scan, sort and keep your printed photos")
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
    return path


def old_folder():
    """The folder the existing ReCollect shortcut starts in (the source version's folder), or None."""
    for csidl in (shellcon.CSIDL_PROGRAMS, shellcon.CSIDL_DESKTOPDIRECTORY):
        lnk = _folder(csidl) / "ReCollect.lnk"
        if lnk.exists():
            try:
                link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                                  shell.IID_IShellLink)
                link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(lnk))
                return Path(link.GetWorkingDirectory())
            except pythoncom.com_error:
                pass
    return None


def ensure_for_exe(exe):
    """Point the Desktop and Start menu shortcuts at ReCollect.exe (the exe holds the icon)."""
    pythoncom.CoInitialize()
    for csidl in (shellcon.CSIDL_PROGRAMS, shellcon.CSIDL_DESKTOPDIRECTORY):
        make(_folder(csidl) / "ReCollect.lnk", target=exe, args="", icon=exe)


if __name__ == "__main__":
    for folder in (shellcon.CSIDL_DESKTOPDIRECTORY, shellcon.CSIDL_PROGRAMS):
        print(make(_folder(folder) / "ReCollect.lnk"))
    if not getattr(sys, "frozen", False):
        print(make(APP_DIR / "ReCollect.lnk"))  # double-click to open, in place of the old ReCollect.bat
