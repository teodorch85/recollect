"""Software versions of Epson Scan 2's Unsharp Mask and Color Restoration (WIA has neither)."""
import cv2
import numpy as np

SHARPEN_LEVELS = {"Off": 0.0, "Low": 0.5, "Medium": 1.0, "High": 1.6}


def restore_color(bgr):
    """Undo fading: stretch each colour channel to full range, then lift saturation.

    Faded prints lose contrast unevenly per channel (usually leaving a red/magenta
    cast), so stretching channels separately removes the cast as well.
    """
    small = cv2.resize(bgr, (600, int(600 * bgr.shape[0] / bgr.shape[1])), interpolation=cv2.INTER_AREA)
    out = np.empty_like(bgr)
    x = np.arange(256, dtype=np.float32)
    for c in range(3):
        lo, hi = np.percentile(small[:, :, c], (0.4, 99.6))
        gain = min(255.0 / max(hi - lo, 1.0), 2.5)  # cap so dark/low-key photos aren't blown out
        lut = np.clip((x - lo) * gain, 0, 255).astype(np.uint8)
        out[:, :, c] = cv2.LUT(bgr[:, :, c], lut)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    hsv[:, :, 1] = cv2.LUT(hsv[:, :, 1], np.clip(x * 1.15, 0, 255).astype(np.uint8))
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def sharpen(bgr, level, dpi):
    """Unsharp mask; radius scales with dpi so 'High' looks the same at 600 or 1200."""
    amount = SHARPEN_LEVELS.get(level, 0.0)
    if amount == 0:
        return bgr
    sigma = 1.2 * dpi / 600
    blurred = cv2.GaussianBlur(bgr, (0, 0), sigma)
    return cv2.addWeighted(bgr, 1 + amount, blurred, -amount, 0)


def process_front(bgr, dpi, sharpen_level, restore):
    if restore:
        bgr = restore_color(bgr)
    return sharpen(bgr, sharpen_level, dpi)
