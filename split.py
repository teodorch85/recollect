"""Split a scan that holds two prints (they lay too close on the glass and were taken for one).

The cut is found in the front: the strip of lid between the two prints is the only line across the
scan with nothing in it. The back was cut from the same spot on the glass, so it is split at the same
place; its white paper against the white lid shows no gap of its own.
Positions are fractions (0-1) of the image's width (axis "x", a vertical cut) or height (axis "y").
"""
import cv2
import numpy as np

WORK_PX = 800      # the gap is looked for in a copy this size
SEARCH = (0.15, 0.85)  # a cut nearer the edge than this would leave a sliver, not a print
MAX_TRIM = 0.3     # lid trimmed off a half's edge is at most this share of it


def _small_gray(img):
    f = min(1.0, WORK_PX / max(img.shape[:2]))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA).astype(np.float32)


def _profile(gray, axis):
    """How much is going on along each line across the image (std of each column for axis x)."""
    std = gray.std(axis=0 if axis == "x" else 1)
    return np.convolve(std, np.ones(3) / 3, mode="same")


def _band(std, i):
    """The run of empty lines around line i: (first, last + 1)."""
    lo, med = std[i], float(np.median(std))
    limit = lo + 0.25 * max(med - lo, 1.0)
    a = b = i
    while a > 0 and std[a - 1] <= limit:
        a -= 1
    while b < len(std) - 1 and std[b + 1] <= limit:
        b += 1
    return a, b + 1


def find_cut(img, axis=None, near=None):
    """(axis, start, end): where the gap between the two prints runs, as fractions. axis None tries
    both ways and keeps the clearer gap; near (a fraction) looks for the gap closest to there."""
    gray = _small_gray(img)
    best = None
    for ax in ((axis,) if axis else ("x", "y")):
        std = _profile(gray, ax)
        n = len(std)
        if near is None:
            lo, hi = int(SEARCH[0] * n), int(SEARCH[1] * n)
        else:  # where you clicked, give or take a few lines
            c = int(near * n)
            lo, hi = max(0, c - n // 40), min(n, c + n // 40 + 1)
        i = lo + int(np.argmin(std[lo:hi]))
        score = std[i] / max(float(np.median(std)), 1.0)  # an empty line stands out from the rest
        a, b = _band(std, i) if near is None or score < 0.5 else (i, i + 1)
        if best is None or score < best[0]:
            best = (score, ax, a / n, b / n)
    return best[1:]


def _trim(img, lid_mean, lid_std):
    """Cut strips of lid off the edges (left by the gap, or by a smaller print beside a bigger one)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape

    def is_lid(line):
        return line.std() <= lid_std and abs(line.mean() - lid_mean) <= 12

    top, bottom, left, right = 0, h, 0, w
    while top < h * MAX_TRIM and is_lid(g[top, left:right]):
        top += 1
    while bottom > h * (1 - MAX_TRIM) and is_lid(g[bottom - 1, left:right]):
        bottom -= 1
    while left < w * MAX_TRIM and is_lid(g[top:bottom, left]):
        left += 1
    while right > w * (1 - MAX_TRIM) and is_lid(g[top:bottom, right - 1]):
        right -= 1
    return img[top:bottom, left:right]


def cut(img, axis, start, end):
    """The two prints of a front, without the lid between and around them."""
    n = img.shape[1] if axis == "x" else img.shape[0]
    a, b = int(round(start * n)), max(int(round(end * n)), int(round(start * n)) + 1)
    gap = img[:, a:b] if axis == "x" else img[a:b]
    g = cv2.cvtColor(gap, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lines = g.std(axis=0 if axis == "x" else 1)  # each line along the gap
    lid_mean, lid_std = float(g.mean()), float(np.percentile(lines, 90)) * 1.5 + 3
    first, second = (img[:, :a], img[:, b:]) if axis == "x" else (img[:a], img[b:])
    return _trim(first, lid_mean, lid_std), _trim(second, lid_mean, lid_std)


def cut_plain(img, axis, at):
    """The two halves of a back, cut at a fraction (no trimming: only its text matters)."""
    n = img.shape[1] if axis == "x" else img.shape[0]
    i = min(max(int(round(at * n)), 1), n - 1)
    return (img[:, :i], img[:, i:]) if axis == "x" else (img[:i], img[i:])
