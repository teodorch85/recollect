"""Made-up prints and beds for the tests: textured "pictures" on a plain lid, like a real scan."""
import cv2
import numpy as np

LID = 228  # brightness of the empty lid


def picture(h, w, seed=0, border=0, sky=False):
    """A print's picture: texture at several scales (detectors need edges, like in a real photo)."""
    rng = np.random.default_rng(seed)
    p = np.zeros((h, w, 3), np.float32)
    for s in (60, 15, 4):
        p += cv2.resize(rng.random((h // s + 1, w // s + 1, 3), np.float32), (w, h), interpolation=cv2.INTER_CUBIC) * 90
    p = np.clip(p - 40, 0, 255).astype(np.uint8)
    if sky:
        p[: h // 2] = (235, 215, 190)
    if border:
        p[:border] = p[-border:] = 245
        p[:, :border] = p[:, -border:] = 245
    return p


def place(canvas, p, cx, cy, angle=0.0):
    """Lay print p on the canvas, centred at (cx, cy), turned `angle` degrees anticlockwise.
    Returns its true rect as the detectors report it."""
    h, w = p.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    m[:, 2] += (cx - w / 2, cy - h / 2)
    size = canvas.shape[1::-1]
    mask = cv2.warpAffine(np.full((h, w), 255, np.uint8), m, size)
    canvas[mask > 128] = cv2.warpAffine(p, m, size)[mask > 128]
    return (cx, cy), (w, h), -angle


def lid(h, w, seed=1):
    rng = np.random.default_rng(seed)
    return np.clip(LID + rng.integers(-3, 4, (h, w, 3)), 0, 255).astype(np.uint8)


def jpeg(path, img):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(path.suffix or ".jpg", img)[1].tofile(str(path))
