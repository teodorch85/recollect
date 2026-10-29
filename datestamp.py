"""Read the orange date a camera printed into the picture (e.g. '94 7 15), common in the 80s-2000s.

The stamp sits near a corner of the frame (bottom right, or another corner when the camera was held
upright), in glowing orange-red digits. We look for a short row of such digits in each corner and
decode them segment by segment (nearly all stamps are seven-segment digits, which Windows OCR
misreads); Windows OCR is only a fallback for other fonts. Nothing found -> "".
"""
import re
from datetime import date

import cv2
import numpy as np

import ocr
from organizer import full_year

WORK_DPI = 300
CORNER = (0.45, 0.28)  # share of the long / short side searched at each corner
DIGIT_MM = (1.0, 6.0)  # plausible height of the stamp's digits on the print
FIRST_YEAR = 1975  # date-imprinting compacts appeared in the late 70s
MAX_OCR = 4  # fallback OCR calls per photo; orange scenery can produce many candidate rows


def _orange(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    hue = (h <= 28) | (h >= 165)  # OpenCV hue runs 0-180: red-orange-amber
    return (hue & (s >= 90) & (v >= 140)).astype(np.uint8) * 255


def _digit_row(mask, mm):
    """The longest horizontal row of digit-sized orange text: (digit boxes left to right, small marks
    such as the apostrophe in '94), or None. Boxes are (x, y, w, h)."""
    # smear the marks sideways into text lines, then keep the biggest group of lines on one baseline
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, int(1.2 * mm)), max(1, int(0.6 * mm))))
    stats = [tuple(int(v) for v in s[:4]) for s in
             cv2.connectedComponentsWithStats(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k))[2][1:]]
    lines = [s for s in stats if DIGIT_MM[0] * mm <= s[3] <= DIGIT_MM[1] * mm]
    best = []
    for a in lines:
        cy, ah = a[1] + a[3] / 2, a[3]
        row = [m for m in lines if abs(m[1] + m[3] / 2 - cy) < 0.25 * ah and 0.75 * ah <= m[3] <= 1.33 * ah]
        if sum(m[2] for m in row) > sum(m[2] for m in best):
            best = row
    if not best:
        return None
    x0, x1 = min(m[0] for m in best), max(m[0] + m[2] for m in best)
    y0, y1 = min(m[1] for m in best), max(m[1] + m[3] for m in best)
    band = mask[y0:y1, x0:x1] > 0
    # characters are separated by empty columns (a seven-segment digit never has one inside it)
    cols = np.flatnonzero(np.diff(np.concatenate(([0], band.any(axis=0).astype(np.int8), [0]))))
    boxes = []
    for c0, c1 in zip(cols[::2], cols[1::2]):
        ys = np.flatnonzero(band[:, c0:c1].any(axis=1))
        boxes.append((x0 + int(c0), y0 + int(ys[0]), int(c1 - c0), int(ys[-1] - ys[0] + 1)))
    h = max(b[3] for b in boxes)
    digits = [b for b in boxes if b[3] >= 0.7 * h]
    # apostrophe: small and near the top, but not a speck of noise
    ticks = [b for b in boxes if 0.15 * h <= b[3] < 0.5 * h and b[2] >= 0.06 * h and b[1] < y0 + 0.4 * h]
    if len(digits) < 3 or not 2.5 * h <= x1 - x0 <= 25 * h:  # a date is several characters, not a whole edge
        return None
    return digits, ticks


# lit segments (a top, b top right, c bottom right, d bottom, e bottom left, f top left, g middle) -> digit;
# includes the variants stamp fonts use for 6, 7 and 9
SEGMENTS = {"abcdef": "0", "bc": "1", "abdeg": "2", "abcdg": "3", "bcfg": "4", "acdfg": "5", "acdefg": "6",
            "cdefg": "6", "abc": "7", "abcf": "7", "abcdefg": "8", "abcdfg": "9", "abcfg": "9"}
ZONES = {"a": (0.25, 0.75, 0.0, 0.2), "g": (0.25, 0.75, 0.4, 0.6), "d": (0.25, 0.75, 0.8, 1.0),
         "f": (0.0, 0.3, 0.12, 0.42), "b": (0.7, 1.0, 0.12, 0.42), "e": (0.0, 0.3, 0.58, 0.88),
         "c": (0.7, 1.0, 0.58, 0.88)}


def _decode(mask, digits, ticks):
    """Read seven-segment digits from their boxes: "'94 7 15", or None if any digit is unreadable."""
    h = float(np.median([d[3] for d in digits]))
    cell = float(np.median([d[2] for d in digits if d[2] >= 0.45 * d[3]] or [0.55 * h]))
    out, prev_end = "", None
    for x, y, w, dh in digits:
        start = x + w - cell if w < 0.45 * dh else x  # a 1 is drawn on the right of its digit cell
        if prev_end is not None and start - prev_end > 0.45 * h:
            out += " "
        if any((prev_end is None or t[0] >= prev_end - 1) and t[0] + t[2] <= x + 0.2 * w for t in ticks):
            out += "'"
        if w < 0.45 * dh:  # a lone vertical bar
            out += "1"
        else:
            box = mask[y:y + dh, x:x + w] > 0
            lit = "".join(k for k, (x0, x1, y0, y1) in sorted(ZONES.items())
                          if box[int(y0 * dh):max(int(y1 * dh), int(y0 * dh) + 1),
                                 int(x0 * w):max(int(x1 * w), int(x0 * w) + 1)].mean() > 0.25)
            if lit not in SEGMENTS:
                return None
            out += SEGMENTS[lit]
        prev_end = x + w
    return out


def _corners(bgr):
    """Orange masks of each corner, turned so a stamp's text would run horizontally (4 ways)."""
    H, W = bgr.shape[:2]
    long_, short = CORNER
    cw, ch = (int(W * long_), int(H * short)) if W >= H else (int(W * short), int(H * long_))
    for x, y in ((W - cw, H - ch), (0, H - ch), (W - cw, 0), (0, 0)):
        mask = _orange(bgr[y:y + ch, x:x + cw])
        if cv2.countNonZero(mask) < 20:
            continue
        for turn in (None, cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
            yield mask if turn is None else cv2.rotate(mask, turn)


def _ocr_image(mask, digits):
    """Black-on-white crop of the digit row for Windows OCR (fallback for stamps that aren't seven-segment)."""
    h = int(np.median([d[3] for d in digits]))
    x0, y0 = digits[0][0], min(d[1] for d in digits)
    x1, y1 = max(d[0] + d[2] for d in digits), max(d[1] + d[3] for d in digits)
    pad = int(h * 0.6)
    text = mask[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    text = cv2.dilate(text, np.ones((3, 3), np.uint8))
    text = cv2.resize(255 - text, None, fx=48 / h, fy=48 / h, interpolation=cv2.INTER_AREA)
    text = cv2.copyMakeBorder(text, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(text, cv2.COLOR_GRAY2BGR)


FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "i": "1",
                       "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8", "g": "9", "q": "9", "’": "'",
                       "`": "'", "‘": "'", "´": "'"})


def parse(text, today=None):
    """'94 7 15' / '15 7 '94' / '7 15 94' -> '1994-07-15'; only the year if day and month are
    ambiguous; '' if it doesn't read as a date."""
    today = today or date.today()
    text = text.translate(FIXES)
    groups = re.findall(r"('?)(\d{1,4})", text)
    if len(groups) != 3:
        return ""
    nums = [int(g) for _, g in groups]
    marked = [i for i, (q, _) in enumerate(groups) if q]  # the apostrophe marks the year: '94
    found = set()
    for yi, mi, di in ((0, 1, 2), (2, 1, 0), (2, 0, 1)):  # Y M D, D M Y, M D Y
        if marked and marked != [yi]:
            continue
        y = full_year(nums[yi], today.year)
        try:
            d = date(y, nums[mi], nums[di])
        except ValueError:
            continue
        if FIRST_YEAR <= y and d <= today:
            found.add(d)
    if not found:
        return ""
    if len(found) == 1:
        return found.pop().isoformat()
    years = {d.year for d in found}
    return str(years.pop()) if len(years) == 1 else ""


def read(bgr, dpi):
    """The camera date printed on a front, as 'YYYY-MM-DD' (or just 'YYYY'), or ''."""
    small = cv2.resize(bgr, None, fx=WORK_DPI / dpi, fy=WORK_DPI / dpi, interpolation=cv2.INTER_AREA)
    mm = WORK_DPI / 25.4
    unreadable = []
    for mask in _corners(small):
        row = _digit_row(mask, mm)
        if row is None:
            continue
        text = _decode(mask, *row)
        if text is None:  # not seven-segment digits: maybe another font, for OCR below
            unreadable.append((mask, row[0]))
        elif parse(text):  # seven-segment decoding: exact and fast
            return parse(text)
    # other fonts: Windows OCR (each row's 180° turn is in the list too, so this covers upside down)
    for mask, digits in unreadable[:MAX_OCR]:
        found = parse(ocr.read_line(_ocr_image(mask, digits)))
        if found:
            return found
    return ""
