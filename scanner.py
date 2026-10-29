"""Drive the Epson Perfection V600 through Windows WIA (no Epson Scan window needed)."""
import gc

import numpy as np
import cv2
import pythoncom
import win32com.client

FMT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

# WIA property ids
INTENT, BITS, XRES, YRES, XPOS, YPOS, XEXT, YEXT = 6146, 4104, 6147, 6148, 6149, 6150, 6151, 6152
INTENT_COLOR, INTENT_GRAY = 1, 2


class ScannerError(RuntimeError):
    pass


AGAIN = "Nothing was saved — press Space to try again."
# Windows scanner (WIA) error codes -> what to do about them
WIA_ERRORS = {
    0x80210001: "The scan was interrupted. Make sure the scanner is on and the lid is closed. " + AGAIN,
    0x80210005: "The scanner is offline. Switch it on and check the USB cable. " + AGAIN,
    0x80210006: "The scanner is busy. Close Epson Scan / Epson Scan 2 if it is open. " + AGAIN,
    0x80210007: "The scanner is still warming up. Wait a few seconds. " + AGAIN,
    0x8021000A: "Lost contact with the scanner. Check that it is on and the USB cable is plugged in. " + AGAIN,
    0x8021000C: "The scanner reported a settings problem. Switch it off and on again. " + AGAIN,
    0x8021000D: "The scanner is locked. Check the transport lock switch on its side is unlocked. " + AGAIN,
    0x8021000E: "The scanner driver had a problem. Switch the scanner off and on again. " + AGAIN,
    0x80210015: "Scanner not found. Is the V600 switched on and connected by USB? " + AGAIN,
    0x80210016: "The scanner lamp is off. Switch the scanner off and on again. " + AGAIN,
}


def _friendly(err):
    """A plain message for a COM/WIA error instead of its raw tuple of numbers."""
    codes = [err.hresult]
    if err.excepinfo and len(err.excepinfo) > 5:
        codes.append(err.excepinfo[5])  # the WIA code usually sits inside excepinfo
    for code in codes:
        msg = WIA_ERRORS.get((code or 0) & 0xFFFFFFFF)
        if msg:
            return msg
    detail = err.excepinfo[2] if err.excepinfo and err.excepinfo[2] else err.strerror
    return f"The scanner reported a problem ({detail}). Check it is on and the lid is closed. " + AGAIN


def _find_device(name_hint="V600"):
    dm = win32com.client.Dispatch("WIA.DeviceManager")
    seen = []
    for info in dm.DeviceInfos:
        name = info.Properties("Name").Value
        if info.Type == 1 and name_hint.lower() in name.lower():
            return info
        seen.append(name)
    others = f" Windows sees only: {', '.join(seen)}." if seen else ""
    raise ScannerError("Scanner not found. Is the V600 switched on and connected by USB? If it is, try "
                       "Settings ▸ Scanning ▸ Reset scanner, or switch it off and on." + others)


def _prop(item, pid):
    for p in item.Properties:
        if p.PropertyID == pid:
            return p
    raise KeyError(pid)


def scan_bed(dpi, gray=False, region=None, background=None):
    """Scan the bed at `dpi` and return a BGR numpy image the size of the whole bed.

    gray=True scans 8-bit greyscale. `region` = (x, y, w, h) in inches limits the
    scan to that area so the head travels less; the rest of the returned image is
    filled from `background` (e.g. the empty-bed reference) or white.
    Safe to call from a worker thread (initialises COM for it).
    """
    pythoncom.CoInitialize()
    try:
        return _scan(dpi, gray, region, background)
    except pythoncom.com_error as e:  # e.g. scanner switched off or lid opened mid-scan
        raise ScannerError(_friendly(e)) from None
    finally:
        gc.collect()  # release COM objects before uninitialising COM
        pythoncom.CoUninitialize()


def _scan(dpi, gray, region, background):
    dev = _find_device().Connect()
    item = dev.Items[1]
    _prop(item, INTENT).Value = INTENT_GRAY if gray else INTENT_COLOR
    _prop(item, BITS).Value = 8 if gray else 24
    _prop(item, XRES).Value = dpi
    _prop(item, YRES).Value = dpi
    # Extents are in pixels at the current resolution (and shrink with the start
    # position), so reset the start and read the full size after setting the dpi.
    _prop(item, XPOS).Value = 0
    _prop(item, YPOS).Value = 0
    full_w, full_h = _prop(item, XEXT).SubTypeMax, _prop(item, YEXT).SubTypeMax
    x, y, w, h = 0, 0, full_w, full_h
    if region is not None:
        x = max(0, min(int(region[0] * dpi), full_w - 1))
        y = max(0, min(int(region[1] * dpi), full_h - 1))
        w = max(1, min(int(region[2] * dpi), full_w - x))
        h = max(1, min(int(region[3] * dpi), full_h - y))
    _prop(item, XPOS).Value = x
    _prop(item, YPOS).Value = y
    _prop(item, XEXT).Value = w
    _prop(item, YEXT).Value = h
    image_file = item.Transfer(FMT_BMP)
    data = np.frombuffer(bytes(image_file.FileData.BinaryData), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ScannerError("Scanner returned an unreadable image.")
    if region is None:
        return img
    if background is not None:
        bed = cv2.resize(background, (full_w, full_h), interpolation=cv2.INTER_LINEAR)
    else:
        bed = np.full((full_h, full_w, 3), 255, np.uint8)
    ih, iw = min(img.shape[0], full_h - y), min(img.shape[1], full_w - x)
    bed[y:y + ih, x:x + iw] = img[:ih, :iw]
    return bed


# Restart Windows' scanner service and reconnect the scanner's USB device, as if it had been
# unplugged and plugged in again. Exit codes: 0 done, 2 no Epson scanner found, 1 anything else.
RESET_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $devices = @(Get-PnpDevice | Where-Object { $_.Present -and
        $_.Class -eq 'Image' -and $_.FriendlyName -match 'EPSON|Perfection' })  # disabled ones too
    if ($devices.Count -eq 0) { Start-Service stisvc; exit 2 }
    Stop-Service stisvc -Force
    foreach ($d in $devices) {
        try {
            if ($d.Status -ne 'Error') { Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false }
            Start-Sleep -Seconds 2
        } finally {  # never leave the scanner switched off in Windows
            Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false
        }
    }
    Start-Sleep -Seconds 2
    Start-Service stisvc
    exit 0
} catch {
    try { Start-Service stisvc } catch {}
    exit 1
}
"""


def reset_scanner():
    """Run RESET_SCRIPT as administrator (Windows asks for permission) and wait for it."""
    import base64
    import win32event
    import win32process
    import pywintypes
    from win32com.shell import shell, shellcon
    encoded = base64.b64encode(RESET_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        info = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                                    lpFile="powershell.exe", nShow=0,
                                    lpParameters=f"-NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}")
    except pywintypes.error as e:
        if e.winerror == 1223:  # ERROR_CANCELLED: "No" in the Windows permission prompt
            raise ScannerError("Reset cancelled: Windows needs your permission to restart the scanner.") from None
        raise ScannerError(f"Couldn't start the reset ({e.strerror}).") from None
    handle = info["hProcess"]
    win32event.WaitForSingleObject(handle, 60_000)
    code = win32process.GetExitCodeProcess(handle)
    handle.Close()
    if code == 2:
        raise ScannerError("Windows doesn't see the scanner. Check it is switched on and the USB cable is "
                           "plugged in, then try again.")
    if code != 0:
        raise ScannerError("The reset didn't finish. Switch the scanner off, wait 10 seconds and switch it on.")
