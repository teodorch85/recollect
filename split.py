"""Split a scan that holds two prints (they lay too close on the glass and were taken for one).

The cut is a straight line, at any slant: prints that touch are rarely square to each other, so the
lid between them is often a wedge. It is found in the front, where the lid is the only line across
the scan with nothing in it; then each print is cut out along its own edges, like on the bed.
The back was cut from the same spot on the glass, so the same line splits it (its white paper against
the white lid shows no gap of its own).
A line is two points (x, y) in fractions (0-1) of the image's width and height.
"""
import cv2
import numpy as np

import detect

WORK_PX = 800          # the gap is looked for in a copy this size
SLANTS = np.tan(np.radians(np.arange(-15, 15.5, 0.5)))  # how far from straight the gap may run
SEARCH = (0.12, 0.88)  # a cut nearer the edge than this would leave a sliver, not a print
BUSY = 12              # brightness step (0-255) per pixel that counts as picture, not lid


def _work(img):
    f = min(1.0, WORK_PX / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0), f


def _busy(gray):
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) / 4
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) / 4
    return cv2.dilate((np.hypot(gx, gy) > BUSY).astype(np.uint8), np.ones((3, 3), np.uint8))


def _best_line(busy, near=None):
    """(score, c, slant) of the emptiest line x = c + slant * (y - h/2) across busy (rows = y)."""
    h, w = busy.shape
    ys = np.arange(h)
    dy = ys - h / 2
    if near is None:
        cs = np.arange(int(SEARCH[0] * w), int(SEARCH[1] * w))
    best = None
    for t in SLANTS:
        if near is not None:  # lines through (about) the point you clicked
            c0 = near[0] * w - t * (near[1] * h - h / 2)
            cs = np.arange(int(c0 - w / 50), int(c0 + w / 50) + 1)
        xs = np.rint(cs[:, None] + t * dy[None, :]).astype(int)
        inside = (xs >= 0) & (xs < w)
        hits = busy[ys[None, :], np.clip(xs, 0, w - 1)]
        score = (hits * inside).sum(axis=1) / h + (1 - inside.mean(axis=1))  # leaving the image counts as busy
        i = lo = hi = int(np.argmin(score))
        while lo > 0 and score[lo - 1] <= score[i] + 0.002:
            lo -= 1
        while hi < len(score) - 1 and score[hi + 1] <= score[i] + 0.002:
            hi += 1
        mid = (lo + hi) // 2  # middle of the empty band, not its edge
        if best is None or score[mid] < best[0] - 1e-9:
            best = (float(score[mid]), float(cs[mid]), float(t))
    return best


def find_cut(img, near=None):
    """The line between the two prints: ((x0, y0), (x1, y1)) in fractions. near: (x, y) fraction of a
    point you clicked: the best line through about there."""
    busy = _busy(_work(img)[0])
    h, w = busy.shape
    down = _best_line(busy, near)  # lines running top to bottom
    across = _best_line(busy.T, near[::-1] if near else None)  # left to right
    if down[0] <= across[0]:
        _, c, t = down
        return ((c - t * h / 2) / w, 0.0), ((c + t * h / 2) / w, 1.0)
    _, c, t = across
    return (0.0, (c - t * w / 2) / h), (1.0, (c + t * w / 2) / h)


def line_through(p, q):
    """The line through two points you dragged between (fractions), reaching the image's edges."""
    (x0, y0), (x1, y1) = p, q
    if abs(x1 - x0) < 1e-6 and abs(y1 - y0) < 1e-6:
        return None
    if abs(y1 - y0) >= abs(x1 - x0):  # steep: from top edge to bottom edge
        t = (x1 - x0) / (y1 - y0)
        return (x0 - t * y0, 0.0), (x0 + t * (1 - y0), 1.0)
    t = (y1 - y0) / (x1 - x0)
    return (0.0, y0 - t * x0), (1.0, y0 + t * (1 - x0))


def _sides(shape, line):
    """Masks of the two sides of the line, the left or top one first."""
    h, w = shape[:2]
    (x0, y0), (x1, y1) = line
    yy, xx = np.ogrid[0:h, 0:w]
    s = (x1 - x0) * w * (yy - y0 * h) - (y1 - y0) * h * (xx - x0 * w) > 0
    sides = [s, ~s]
    # left/top first: compare where each side's middle is, along the line's normal
    first = [(np.nonzero(m)[1].mean() + np.nonzero(m)[0].mean()) if m.any() else 0 for m in sides]
    return sides if first[0] <= first[1] else sides[::-1]


def boxes(img, line, dpi):
    """Where each print lies, as rotated rects in img's pixels: its own edges, found on its side of the line."""
    gray, f = _work(img)
    work_dpi = dpi * f
    rects = []
    for side in _sides(gray.shape, line):
        side_u8 = side.astype(np.uint8) * 255
        edge = cv2.erode(side_u8, np.ones((5, 5), np.uint8))  # pixels along the cut are lid, or the other print
        band = (side_u8 > 0) & (edge == 0)
        lid = float(np.median(gray[band])) if band.any() else float(np.percentile(gray, 95))
        mask = (cv2.absdiff(gray, np.full_like(gray, int(lid))) > 8).astype(np.uint8) * 255
        mask |= cv2.dilate(cv2.Canny(gray, 15, 45), np.ones((3, 3), np.uint8))
        mask &= edge
        k = max(3, int(0.09 * work_dpi)) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
        k = max(3, int(0.15 * work_dpi)) | 1
        filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, np.ones((k, k), np.uint8)) & edge
        contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        big = max(contours, key=cv2.contourArea) if contours else None
        if big is None or cv2.contourArea(big) < 0.3 * side.sum():  # nothing print-like: keep the whole side
            big = cv2.findNonZero(side_u8)
        (cx, cy), (w, h), a = cv2.minAreaRect(big)
        while a <= -45:
            a, w, h = a + 90, h, w
        while a > 45:
            a, w, h = a - 90, h, w
        rects.append(detect._refine(img, ((cx / f, cy / f), (w / f, h / f), a), dpi, lid))  # exact edges
    return rects


def cut(img, line, dpi):
    """The two prints of a front, straightened and without lid, the left or top one first."""
    return [detect.crop(img, r, dpi, inset_mm=0.3) for r in boxes(img, line, dpi)]


def cut_back(img, line):
    """The two halves of a back (no straightening: only its text matters), the other side painted out."""
    out = []
    paper = np.median(img.reshape(-1, img.shape[2]), axis=0)
    for side in _sides(img.shape, line):
        ys, xs = np.nonzero(side)
        half = img.copy()
        half[~side] = paper
        out.append(half[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
    return out
