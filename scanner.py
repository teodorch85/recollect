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
    for info in dm.DeviceInfos:
        if info.Type == 1 and name_hint.lower() in info.Properties("Name").Value.lower():
            return info
    raise ScannerError("Scanner not found. Is the V600 switched on and connected by USB?")


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
