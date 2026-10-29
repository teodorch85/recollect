"""Updates for ReCollect.exe: look for a newer release on GitHub, download it, and swap it in.

Each release on GitHub carries ReCollect-<version>.zip, built by .github/workflows/release.yml.
The running exe can't overwrite its own files, so a small PowerShell helper waits for ReCollect to
close, copies the new files over the install folder, and starts it again. Settings, calibration
and photos live elsewhere (DATA_DIR, the photos folder) and are never touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "teodorch85/recollect"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
FROZEN = getattr(sys, "frozen", False)  # running as ReCollect.exe (PyInstaller), not from source
INSTALL_DIR = Path(sys.executable).parent if FROZEN else None
WORK = Path(tempfile.gettempdir()) / "ReCollect-update"


class UpdateError(RuntimeError):
    pass


def _newer(a, b):
    try:
        return tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))
    except ValueError:
        return False


def latest_release(current):
    """(version, zip url, release page) of a release newer than `current`, or None.
    Raises UpdateError when GitHub can't be reached."""
    req = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "ReCollect"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rel = json.load(r)
    except OSError as e:
        raise UpdateError(f"Couldn't reach GitHub ({getattr(e, 'reason', e)}).") from None
    version = rel.get("tag_name", "").lstrip("v")
    zips = [a["browser_download_url"] for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")]
    if not zips or not _newer(version, current):
        return None
    return version, zips[0], rel.get("html_url", RELEASES_PAGE)


def download(url, progress=None):
    """Download and unpack a release; returns the folder holding the new ReCollect.exe."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    zip_path = WORK / "update.zip"
    req = urllib.request.Request(url, headers={"User-Agent": "ReCollect"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(zip_path, "wb") as f:
            size, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and size:
                    progress(done / size)
    except OSError as e:
        raise UpdateError(f"The download failed ({getattr(e, 'reason', e)}). Try again later.") from None
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(WORK / "new")
    except zipfile.BadZipFile:
        raise UpdateError("The download was damaged. Try again.") from None
    found = list((WORK / "new").rglob("ReCollect.exe"))
    if not found:
        raise UpdateError("The download doesn't contain ReCollect.exe.")
    return found[0].parent


HELPER = r"""
param([int]$OldPid, [string]$Source, [string]$Target)
try { Wait-Process -Id $OldPid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
robocopy $Source $Target /E /R:10 /W:1 /NFL /NDL /NJH /NJS | Out-Null
Start-Process -FilePath (Join-Path $Target 'ReCollect.exe') -WorkingDirectory $Target
"""


def install_and_restart(new_dir):
    """Start the helper that copies `new_dir` over the install folder once this process has exited.
    The caller must then close the app."""
    if not FROZEN or not (INSTALL_DIR / "ReCollect.exe").exists():
        raise UpdateError("Automatic updates work only in ReCollect.exe.")
    try:  # e.g. under Program Files, where only an administrator may write
        (INSTALL_DIR / ".write-test").write_text("")
        (INSTALL_DIR / ".write-test").unlink()
    except OSError:
        raise UpdateError(f"ReCollect can't update itself in {INSTALL_DIR}. Move the ReCollect folder somewhere "
                          "of your own, e.g. Documents, and start it from there.") from None
    script = WORK / "apply.ps1"
    script.write_text(HELPER, encoding="utf-8")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script), "-OldPid", str(os.getpid()), "-Source", str(new_dir),
                      "-Target", str(INSTALL_DIR)],
                     creationflags=0x08000000)  # CREATE_NO_WINDOW
