"""Create ReCollect shortcuts on the Desktop, in the Start menu and in this folder (run again if the
folder moves).

They carry the app's icon and taskbar ID, so pinning one to the taskbar and the running window
share a single taskbar button.
"""
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell, shellcon

APP_DIR = Path(__file__).resolve().parent
APP_ID = "ReCollect.PhotoScans"  # same as photo_scanner._own_taskbar_entry


def make(path):
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                      shell.IID_IShellLink)
    link.SetPath(str(APP_DIR / ".venv" / "Scripts" / "pythonw.exe"))
    link.SetArguments("photo_scanner.py")
    link.SetWorkingDirectory(str(APP_DIR))
    link.SetIconLocation(str(APP_DIR / "ReCollect.ico"), 0)
    link.SetDescription("Scan, sort and keep your printed photos")
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
    return path


if __name__ == "__main__":
    for folder in (shellcon.CSIDL_DESKTOPDIRECTORY, shellcon.CSIDL_PROGRAMS):
        print(make(Path(shell.SHGetFolderPath(0, folder, None, 0)) / "ReCollect.lnk"))
    print(make(APP_DIR / "ReCollect.lnk"))  # double-click to open, in place of the old ReCollect.bat
