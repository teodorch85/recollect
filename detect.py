"""Find individual prints on a bed scan, straighten/crop them, and pair fronts with backs."""
from dataclasses import dataclass, replace

import cv2
import numpy as np

import organizer

WORK_DPI = 100          # detection runs on a downscaled copy at this resolution
GAP_DPI = 200           # finer copy used to find narrow gaps between prints that nearly touch
MIN_SIDE_IN = 1.0       # ignore anything smaller than 1 inch on a side
ODD_AREA_CM2 = 200      # a rect this big (cm2) could hold two prints; if it is no standard size, ask
TRIM_MAX_MM = 16        # the widest strip of plain lid trimmed off a side of a print (find_photos)
TRIM_STEP = 10          # ... and it must end in a visible edge: the print differs from the strip by this much
INSET_MM = 0.6          # shave this much off each edge so no lid shows at the border


def _to_work(img, dpi):
    f = WORK_DPI / dpi
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)


def _gray_blur(img):
    return cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)


@dataclass(frozen=True)
class Sensitivity:
    """How hard the splitters look for prints that touch. Large prints with a very narrow gap, or a
    step of a millimetre between them, need FINER; it also sees things that aren't there, so it is
    only used on request (and to cross-check a normal run)."""
    lid_min: int       # a pixel brighter than this can be lid; a gap in shadow is darker than open lid
    tophat: int        # how much brighter than its surroundings a thin line of lid must be
    hough: float       # inches of line that must vote for a gap line
    line_in: float     # shortest gap line (inches)
    line_gap: float    # a gap line may be broken by this much (inches)
    angle: float       # gap lines may be this many degrees off straight
    min_side: float    # smallest print a gap line may leave behind (inches)
    straight: float    # work pixels an edge of one print may wander off a straight line
    step: float        # a side must be this many work pixels off one straight line to show two prints
    ratio: float       # ... and beat the wobble of the straight parts by this factor


NORMAL = Sensitivity(lid_min=190, tophat=20, hough=0.8, line_in=1.5, line_gap=0.05, angle=8,
                     min_side=1.5, straight=1.5, step=1.5, ratio=3.0)
FINER = Sensitivity(lid_min=150, tophat=12, hough=0.5, line_in=1.0, line_gap=0.1, angle=10,
                    min_side=1.2, straight=1.5, step=1.1, ratio=2.0)  # step: on big blobs only (_stacked)



def find_photos(img, dpi, reference=None, refine=True, finer=False):
    """Return rotated rects ((cx, cy), (w, h), angle) in full-resolution pixels.

    `reference` is an empty-bed scan (any dpi); subtracting it makes white
    borders and white photo backs stand out from the white lid. Pass
    refine=False for backs: plain white paper looks like lid to the edge snapper.
    finer=True looks harder for prints that nearly touch (see FINER).
    """
    return _detect(img, dpi, reference, refine, FINER if finer else NORMAL)[0]


def find_prints(img, dpi, reference=None, finer=False):
    """(rects, unsure): like find_photos, plus the indices of the rects the program is not sure about,
    for the user to check: a rect big enough to be two prints that is not a standard print size, a blob
    that hinted at two prints without proof, or a print that the finer settings see differently.
    Only a normal run looks for hints and cross-checks with the finer settings (a finer run is itself
    the second opinion, and hints are everywhere with its settings)."""
    rects, doubts = _detect(img, dpi, reference, True, FINER if finer else NORMAL)
    unsure = {i for i, d in enumerate(doubts) if (d and not finer) or odd_size(rects[i], dpi)}  # finer hints everywhere
    if not finer and rects:
        again = _detect(img, dpi, reference, False, FINER)[0]
        unsure |= {i for i, r in enumerate(rects) if not any(_same_print(r, o, dpi) for o in again)}
    return rects, sorted(unsure)


def odd_size(rect, dpi):
    """True for a rect that could hold two prints (long and thin, or a big area) yet is no standard
    print size: probably two prints taken for one (or a odd print: the user decides)."""
    (w, h) = rect[1]
    a, b = sorted((w / dpi * 2.54, h / dpi * 2.54))
    if organizer.print_size(w, h, dpi)[1]:
        return False
    return b >= 1.8 * a or a * b >= ODD_AREA_CM2


def _same_print(r, o, dpi):
    """Two rects that outline the same print: centres within 0.3 in, sides within 10%."""
    close = np.hypot(r[0][0] - o[0][0], r[0][1] - o[0][1]) < 0.3 * dpi
    return close and all(abs(x - y) < 0.1 * max(x, y) for x, y in zip(sorted(r[1]), sorted(o[1])))


def _detect(img, dpi, reference, refine, sens):
    """(rects in reading order, a doubt flag for each: the blob hinted at two prints without proof)."""
    work = _to_work(img, dpi)
    g = _gray_blur(work)
    if reference is not None:
        ref = cv2.resize(reference, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_AREA)
        diff = cv2.absdiff(g, _gray_blur(ref))
        lid = float(np.median(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)))
    else:
        lid = float(np.percentile(g, 95))
        diff = cv2.absdiff(g, np.full_like(g, int(np.median(g))))
    _, mask = cv2.threshold(diff, 6, 255, cv2.THRESH_BINARY)
    edges = cv2.dilate(cv2.Canny(g, 15, 45), np.ones((3, 3), np.uint8))
    mask = cv2.bitwise_or(mask, edges)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    # Fill each outline so a white-bordered print becomes one solid blob.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = _split_touching(contours, filled.shape, _gap_lines(img, dpi, sens), sens)
    rects, doubts = [], []
    for c in contours:
        pieces, maybe = _stacked(c, g, sens)
        for rect in _rects(pieces, dpi):
            rect = _trim_lid(img, rect, dpi, lid)
            rects.append(_refine(img, rect, dpi, lid) if refine else rect)
            doubts.append(maybe)
    order = reading_order(rects, dpi)
    return order, [doubts[next(i for i, r in enumerate(rects) if r is o)] for o in order]


def _rects(contours, dpi):
    """Rotated rects (full resolution) of the print-sized contours found on the work copy."""
    scale = dpi / WORK_DPI
    rects = []
    for c in contours:
        (cx, cy), (w, h), a = cv2.minAreaRect(c)
        if min(w, h) < MIN_SIDE_IN * WORK_DPI:
            continue
        # Keep the correction angle small (within ±45°) whatever OpenCV's convention.
        while a <= -45:
            a += 90
            w, h = h, w
        while a > 45:
            a -= 90
            w, h = h, w
        rects.append(((cx * scale, cy * scale), (w * scale, h * scale), a))
    return rects


def reading_order(rects, dpi):
    """Rows top-to-bottom (centres within 1.5" share a row), then left-to-right."""
    rects = sorted(rects, key=lambda r: r[0][1])
    rows = []
    for r in rects:
        if rows and r[0][1] - rows[-1][0][0][1] < 1.5 * dpi:
            rows[-1].append(r)
        else:
            rows.append([r])
    return [r for row in rows for r in sorted(row, key=lambda r: r[0][0])]


def _gap_lines(img, dpi, sens=NORMAL):
    """Long, thin, straight bright lines: the strip of lid showing between prints a few mm apart.

    Returns segments in WORK_DPI coordinates. Only near-horizontal/vertical lines count,
    since gaps follow the edges of roughly straight-placed prints.
    """
    f = GAP_DPI / dpi
    g = cv2.cvtColor(cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    k = int(10 / 25.4 * GAP_DPI) | 1  # gaps (and strips of lid at the bed edge) narrower than ~10 mm
    # Pad with black so a strip of lid between a print and the bed edge also counts as a thin line.
    padded = cv2.copyMakeBorder(g, k, k, k, k, cv2.BORDER_CONSTANT, value=0)
    tophat = cv2.morphologyEx(padded, cv2.MORPH_TOPHAT,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))[k:-k, k:-k]
    thin = ((tophat > sens.tophat) & (g > sens.lid_min)).astype(np.uint8) * 255
    segs = cv2.HoughLinesP(thin, 1, np.pi / 360, threshold=int(sens.hough * GAP_DPI),
                           minLineLength=int(sens.line_in * GAP_DPI), maxLineGap=int(sens.line_gap * GAP_DPI))
    lines = []
    for x1, y1, x2, y2 in (segs.reshape(-1, 4) if segs is not None else []):
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 90
        if min(ang, 90 - ang) > sens.angle:
            continue
        # stretch each end (up to 0.3") so the cut reaches the blob's outer edge, but only over lid:
        # the lid strip along the glass edge ends where the next print begins, and must not cut into it
        d = np.array([x2 - x1, y2 - y1], float)
        d /= np.linalg.norm(d)
        a, b = np.array([x1, y1], float), np.array([x2, y2], float)
        p1 = (a - d * _lid_run(g, a, -d, 0.3 * GAP_DPI, sens.lid_min)) * WORK_DPI / GAP_DPI
        p2 = (b + d * _lid_run(g, b, d, 0.3 * GAP_DPI, sens.lid_min)) * WORK_DPI / GAP_DPI
        lines.append((tuple(p1.astype(int)), tuple(p2.astype(int))))
    return lines


def _lid_run(gray, start, step, limit, lid_min):
    """How many pixels (at most `limit`) one can walk from `start` along the unit vector `step` over
    bright, lid-like pixels. Beyond the scanned area counts as lid."""
    t = np.arange(1, int(limit) + 1)[:, None]
    xy = np.rint(start + t * step).astype(int)
    h, w = gray.shape
    inside = (xy[:, 0] >= 0) & (xy[:, 0] < w) & (xy[:, 1] >= 0) & (xy[:, 1] < h)
    bright = ~inside | (gray[np.clip(xy[:, 1], 0, h - 1), np.clip(xy[:, 0], 0, w - 1)] > lid_min)
    return int(np.argmin(bright)) if not bright.all() else int(limit)


def _is_print_shaped(c, min_side):
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    return min(w, h) >= min_side and cv2.contourArea(c) >= 0.9 * w * h


def _split_touching(contours, shape, lines, sens=NORMAL):
    """Cut blobs along gap lines: separates prints that nearly touch and trims lid strips off edges.

    A cut is kept only when it leaves clean print-sized rectangles plus, at most, thin
    slivers of lid; that stops a white line *inside* a picture (horizon, door frame)
    from splitting it.
    """
    if not lines:
        return contours
    min_side = sens.min_side * WORK_DPI
    sliver = 0.5 * WORK_DPI
    out = []
    for c in contours:
        blob = np.zeros(shape, np.uint8)
        cv2.drawContours(blob, [c], -1, 255, cv2.FILLED)
        cut = blob.copy()
        for p1, p2 in lines:
            cv2.line(cut, p1, p2, 0, 3)
        cut = cv2.morphologyEx(cut, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        pieces, _ = cv2.findContours(cut, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        prints = [p for p in pieces if _is_print_shaped(p, min_side)]
        rest = [p for p in pieces if not _is_print_shaped(p, min_side)]
        slivers_only = all(min(cv2.minAreaRect(p)[1]) < sliver for p in rest)
        if prints and slivers_only and sum(map(cv2.contourArea, prints)) > 0.85 * cv2.contourArea(c):
            out.extend(prints)
        else:
            out.append(c)
    return out


MIN_PIECE_IN = 2.0  # the smallest print _split_stacked will cut off a blob


def _fit_errors(ys, xs):
    """For every way of cutting the rows in two, the squared error of fitting a straight line to each
    part: (err of rows[:i], err of rows[i:]) for each i, via running sums."""
    def running(v):
        return np.concatenate([[0.0], np.cumsum(v)])
    n, sy, sx = running(np.ones_like(ys)), running(ys), running(xs)
    syy, sxx, sxy = running(ys * ys), running(xs * xs), running(ys * xs)

    def err(a, b):  # rows a..b-1 (arrays of indices)
        cnt, y, x = n[b] - n[a], sy[b] - sy[a], sx[b] - sx[a]
        vy = (syy[b] - syy[a]) - y * y / np.maximum(cnt, 1)
        vx = (sxx[b] - sxx[a]) - x * x / np.maximum(cnt, 1)
        cxy = (sxy[b] - sxy[a]) - x * y / np.maximum(cnt, 1)
        return np.maximum(vx - cxy * cxy / np.maximum(vy, 1e-9), 0)
    return err


def _split_stacked(c, gray, sens=NORMAL):
    """[c], or the contours of the two prints lying edge to edge in blob c (see _stacked)."""
    return _stacked(c, gray, sens)[0]


def _stacked(c, gray, sens=NORMAL):
    """Two prints lying edge to edge, with no lid between them, make one blob. Its outline gives them away:
    a side of it is two straight lines with a step or a bend where the prints meet (prints laid by hand
    are never exactly the same size and in line). Returns ([c] or the contours of the two prints, maybe):
    maybe is True when the blob is big enough for two prints and its outline hinted at a step that was
    too weak (or too awkward to cut) to be sure."""
    (cx, cy), (w, h), a = cv2.minAreaRect(c)
    piece = MIN_PIECE_IN * WORK_DPI
    if max(w, h) < 2 * piece or min(w, h) < piece:
        return [c], False
    small = w * h * (2.54 / WORK_DPI) ** 2 < ODD_AREA_CM2  # no room for two prints of a usual size
    if small:  # the outline of one print jogs by up to ~1.2 px: on a small blob a finer step test sees noise
        sens = replace(sens, step=max(sens.step, NORMAL.step), ratio=max(sens.ratio, NORMAL.ratio))
    # Straighten the blob so its long side runs down the rows
    pad = 10
    W, H = int(w) + 2 * pad, int(h) + 2 * pad
    m = cv2.getRotationMatrix2D((cx, cy), a, 1.0)
    m[:, 2] += (W / 2 - cx, H / 2 - cy)
    blob = np.zeros(gray.shape, np.uint8)
    cv2.drawContours(blob, [c], -1, 255, cv2.FILLED)
    flat = cv2.warpAffine(blob, m, (W, H), flags=cv2.INTER_NEAREST) > 0
    pic = cv2.warpAffine(gray, m, (W, H), flags=cv2.INTER_LINEAR)
    across = w > h
    if across:
        flat, pic = flat.T, pic.T
    rows = np.flatnonzero(flat.any(axis=1))
    trim = int(0.25 * WORK_DPI)  # rounded corners
    rows = rows[trim:len(rows) - trim]
    if len(rows) < 2 * piece:
        return [c], False
    sub = flat[rows]
    left = sub.argmax(axis=1).astype(float)
    right = (sub.shape[1] - 1 - sub[:, ::-1].argmax(axis=1)).astype(float)
    ys = rows.astype(float)
    gap = int(0.1 * WORK_DPI)  # rows next to the meeting point: rounded corners again
    cuts = np.arange(int(piece) - trim, len(rows) - int(piece) + trim)
    cuts = cuts[(cuts - gap > 2) & (cuts + gap < len(rows) - 2)]
    if not len(cuts):
        return [c], False
    n = len(rows)
    errs = {}
    for name, xs in (("left", left), ("right", right)):
        err = _fit_errors(ys, xs)
        errs[name] = (err(np.zeros_like(cuts), cuts - gap), err(cuts + gap, np.full_like(cuts, n)),
                      err(np.array([0]), np.array([n]))[0])
    total = sum(e[0] + e[1] for e in errs.values())
    best = int(np.argmin(total))
    k = cuts[best]
    stepped = hinted = False
    for name, (ea, eb, e1) in errs.items():
        rms_a, rms_b = np.sqrt(ea[best] / (k - gap)), np.sqrt(eb[best] / (n - k - gap))
        rms_1 = np.sqrt(e1 / n)
        if max(rms_a, rms_b) > sens.straight:  # a side that isn't straight: torn, curled, or not a print edge
            return [c], False
        wobble = max(rms_a, rms_b, 0.4)
        stepped |= rms_1 >= sens.step and rms_1 >= sens.ratio * wobble
        hinted |= rms_1 >= sens.step and rms_1 >= 0.5 * sens.ratio * wobble  # a single print's outline jogs up to ~1.2
    if not stepped:
        return [c], hinted and not small
    # Where exactly they meet: the strongest straight edge across the picture near there
    seam = rows[k]
    gy = np.abs(cv2.Sobel(pic.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3))
    near = np.r_[max(0, k - 3 * gap):k - gap, k + gap:min(n, k + 3 * gap)]  # not the corners where they meet
    x0, x1 = int(left[near].max()) + 3, int(right[near].min()) - 3
    if x1 - x0 < piece:
        return [c], True
    xs = np.arange(x0, x1)
    best_line, best_score = (seam, 0.0), -1
    for t in np.tan(np.radians(np.arange(-3, 3.01, 0.25))):
        for y in range(seam - 8, seam + 9):
            yy = np.rint(y + t * (xs - (x0 + x1) / 2)).astype(int)
            if yy.min() < 0 or yy.max() >= gy.shape[0]:
                continue
            score = gy[yy, xs].mean()
            if score > best_score:
                best_score, best_line = score, (y, t)
    y, t = best_line
    far = 2 * max(W, H)
    ends = np.array([[-far, y - t * (far + (x0 + x1) / 2)], [far, y + t * (far - (x0 + x1) / 2)]], float)
    if across:
        ends = ends[:, ::-1]
    back = cv2.invertAffineTransform(m)
    ends = ends @ back[:, :2].T + back[:, 2]
    cut = blob.copy()
    cv2.line(cut, tuple(np.rint(ends[0]).astype(int)), tuple(np.rint(ends[1]).astype(int)), 0, 3)
    pieces, _ = cv2.findContours(cut, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pieces = sorted(pieces, key=cv2.contourArea, reverse=True)[:2]
    if len(pieces) < 2 or min(min(cv2.minAreaRect(p)[1]) for p in pieces) < 0.8 * piece:
        return [c], True
    return pieces, False


def _lid_like(strip, axis, lid):
    """Which lines of a strip of the straightened picture look like empty lid: about as bright as the
    lid (even if a bit shadowed) and very plain along their length (axis: the way the mean is taken)."""
    mean, std = strip.mean(axis=axis), strip.std(axis=axis)
    return (mean > lid - 25) & (mean < lid + 4) & (std < 7)


def _strip_width(flags, level, reach, dpi):
    """Pixels of plain lid at the start of a side, given which of its lines look like lid (`flags`) and
    their brightness (`level`). 0 unless the strip stops within `reach` pixels at a clear edge: a plain
    patch inside the picture (sky, a white wall) fills the whole band or ends without one."""
    start = int(1.5 / 25.4 * dpi)  # the very first lines may be the blob's rim, not yet lid
    first = np.flatnonzero(flags[:start + 1])
    if not len(first):
        return 0
    end = first[0]
    while end < reach and flags[end]:
        end += 1
    if end >= reach or end - first[0] < int(1.5 / 25.4 * dpi):
        return 0
    k = max(3, int(1.0 / 25.4 * dpi))
    inner, strip = level[end + 2:end + 2 + k], level[max(first[0], end - k):end]
    if len(inner) < k or abs(inner.mean() - strip.mean()) < TRIM_STEP:
        return 0
    return int(end)


def _trim_lid(img, rect, dpi, lid):
    """Cut strips of plain lid off the sides of a rough rect, wider than _refine looks (it only sees ±2.5 mm).

    A strip of lid beside a print stays glued to it when the print lies a few mm from the glass edge (the
    outline of the blob is then straight there), and the strip is not a gap line. Only a side along the
    edge of the scan is looked at, and only a strip that runs along all of it, looks like lid (see _lid_like)
    and ends in a clear edge goes; the print keeps at least MIN_PIECE_IN each way. So a plain bright area
    inside a picture (grey sky, a white border) is left alone on prints that lie away from the glass edge.
    """
    (cx, cy), (w, h), a = rect
    edges = _on_scan_edge(img.shape, rect, dpi)
    if not any(edges):
        return rect
    pic = crop(img, rect, dpi, inset_mm=0)
    if pic.size == 0:
        return rect
    g = cv2.GaussianBlur(cv2.cvtColor(pic, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    H, W = g.shape
    core_r, core_c = slice(int(0.1 * H), int(0.9 * H)), slice(int(0.1 * W), int(0.9 * W))
    reach = int(TRIM_MAX_MM / 25.4 * dpi)
    cols, rows = _lid_like(g[core_r, :], 0, lid), _lid_like(g[:, core_c], 1, lid)
    col_level, row_level = g[core_r, :].mean(axis=0), g[:, core_c].mean(axis=1)
    left, right = (_strip_width(cols[::s], col_level[::s], reach, dpi) for s in (1, -1))
    top, bottom = (_strip_width(rows[::s], row_level[::s], reach, dpi) for s in (1, -1))
    left, right, top, bottom = (n if at_edge else 0 for n, at_edge in zip((left, right, top, bottom), edges))
    keep = MIN_PIECE_IN * dpi
    if W - left - right < keep:
        left = right = 0
    if H - top - bottom < keep:
        top = bottom = 0
    if not (left or right or top or bottom):
        return rect
    inv = np.linalg.inv(cv2.getRotationMatrix2D((0, 0), a, 1.0)[:, :2])
    dx, dy = inv @ np.array([(left - right) / 2, (top - bottom) / 2])  # straightened offset -> scan coordinates
    return (float(cx + dx), float(cy + dy)), (float(w - left - right), float(h - top - bottom)), float(a)


def _on_scan_edge(shape, rect, dpi, mm=2.0):
    """(left, right, top, bottom) of the straightened rect: is that side within `mm` of the edge of the scan?"""
    (cx, cy), (w, h), a = rect
    inv = np.linalg.inv(cv2.getRotationMatrix2D((0, 0), a, 1.0)[:, :2])
    near = mm / 25.4 * dpi
    out = []
    for ends in (((-w, -h), (-w, h)), ((w, -h), (w, h)), ((-w, -h), (w, -h)), ((-w, h), (w, h))):
        xy = np.array(ends, float) / 2 @ inv.T + (cx, cy)  # the side's two corners on the scan
        gap = np.minimum.reduce([xy[:, 0], xy[:, 1], shape[1] - 1 - xy[:, 0], shape[0] - 1 - xy[:, 1]])
        out.append(bool(gap.max() <= near))
    return tuple(out)


def _refine(img, rect, dpi, lid):
    """Snap each side of a rough rect to the exact print edge, at full resolution.

    Looks ±2.5 mm around each side for "lid-like" lines (plain, about as bright as
    the empty lid, even if a bit shadowed); the print starts just inside the
    innermost one. That works whether lid, a gap or a neighbouring print lies outside.
    """
    (cx, cy), (w, h), a = rect
    m = int(2.5 / 25.4 * dpi)
    big = crop(img, ((cx, cy), (w + 2 * m, h + 2 * m), a), dpi, inset_mm=0)
    g = cv2.GaussianBlur(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    H, W = g.shape
    core_r, core_c = slice(int(0.1 * H), int(0.9 * H)), slice(int(0.1 * W), int(0.9 * W))

    rot = cv2.getRotationMatrix2D((0, 0), a, 1.0)[:, :2]
    inv = np.linalg.inv(rot)

    def outside(offsets, axis):  # lines beyond the scanned area (print pushed against the bed edge)
        pts = np.zeros((len(offsets), 2))
        pts[:, axis] = offsets
        xy = pts @ inv.T + (cx, cy)
        return (xy[:, 0] < 0) | (xy[:, 0] >= img.shape[1]) | (xy[:, 1] < 0) | (xy[:, 1] >= img.shape[0])

    cols = _lid_like(g[core_r, :], 0, lid) | outside(np.arange(W) - W / 2 + 0.5, 0)
    rows = _lid_like(g[:, core_c], 1, lid) | outside(np.arange(H) - H / 2 + 0.5, 1)

    def edge(flags):  # just inside the innermost lid line in the band; no lid there -> keep rough edge
        hits = np.flatnonzero(flags[:2 * m])
        return hits[-1] + 1 if len(hits) else m

    left, right = edge(cols), W - edge(cols[::-1])
    top, bottom = edge(rows), H - edge(rows[::-1])
    shift = np.array([(left + right) / 2 - W / 2, (top + bottom) / 2 - H / 2])
    dx, dy = inv @ shift  # straightened-crop offset -> scan coordinates
    return (float(cx + dx), float(cy + dy)), (float(right - left), float(bottom - top)), float(a)


def crop(img, rect, dpi, inset_mm=INSET_MM):
    """Cut one rotated rect out of a big image and return it straightened."""
    (cx, cy), (w, h), a = rect
    inset = inset_mm / 25.4 * dpi
    box = cv2.boxPoints(rect)
    x, y, bw, bh = cv2.boundingRect(box.astype(np.int32))
    pad = 4
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + bw + pad, img.shape[1]), min(y + bh + pad, img.shape[0])
    sub = img[y0:y1, x0:x1]
    center = (cx - x0, cy - y0)
    m = cv2.getRotationMatrix2D(center, a, 1.0)
    rotated = cv2.warpAffine(sub, m, (sub.shape[1], sub.shape[0]), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
    size = (int(round(w - 2 * inset)), int(round(h - 2 * inset)))
    return cv2.getRectSubPix(rotated, size, center)


def is_blank(back, dpi):
    """True when a photo back has no writing, stamp or print on it."""
    small = _to_work(back, dpi)
    h, w = small.shape[:2]
    m = int(0.06 * min(h, w))
    g = cv2.GaussianBlur(cv2.cvtColor(small[m:h - m, m:w - m], cv2.COLOR_BGR2GRAY), (3, 3), 0)
    ink = np.count_nonzero(g < np.median(g) - 30) / g.size
    return ink < 0.0015


def pair_backs(front_rects, back_rects, dpi):
    """For each front, where its back lies (prints flipped in place).

    The front's exact size is kept. A detected back only moves it, and only when
    it is the same size (so two backs merged into one blob are ignored).
    Otherwise the front's outline is reused as is.
    """
    max_dist = 1.0 * dpi  # centres must be within an inch
    free = list(back_rects)
    paired = []
    for fr in front_rects:
        (cx, cy), (w, h), a = fr
        match = None
        for br in free:
            same_size = abs(br[1][0] - w) < 0.08 * w and abs(br[1][1] - h) < 0.08 * h
            if same_size and np.hypot(br[0][0] - cx, br[0][1] - cy) < max_dist:
                match = br
                break
        if match is not None:
            free.remove(match)
            # find_photos keeps angles within ±45°, so width lies along x for both scans
            paired.append((match[0], (w, h), match[2]))
        else:
            paired.append(((cx, cy), (w, h), -a))  # flipped print mirrors its tilt
    return paired


MIN_GAP_MM = 3.0   # closer than this, two prints may be taken for one
EDGE_MM = 2.0      # the scanner can't see the last mm or so of the glass


PAPER_LEVELS = 3      # paper reads at least this much brighter than the lid (backs: about +5)
PICTURE_SHARE = 0.10  # a print with less picture than this is showing its back


def find_paper(img, dpi, reference):
    """Rects of white paper on the glass, e.g. prints lying back-side down.

    The photo detector looks for pictures and gets lost on white backs (it boxes watermark text),
    but paper reads a few levels brighter than the lid, with a thin shadow line at its edges.
    """
    if reference is None:
        return []
    work = _to_work(img, dpi)
    g = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    ref = cv2.cvtColor(cv2.resize(reference, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_AREA),
                       cv2.COLOR_BGR2GRAY)
    k = max(3, int(0.06 * WORK_DPI) | 1)
    g = cv2.morphologyEx(g, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))  # hide text
    diff = cv2.medianBlur(cv2.subtract(g, ref), k)  # brighter than the lid; darker (shadows) -> 0
    mask = (diff >= PAPER_LEVELS).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)  # watermark holes become paper again
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return reading_order(_rects(contours, dpi), dpi)


def picture_share(img, rect, dpi):
    """Share of a print's area that is picture (colour or mid/dark tones) rather than blank paper;
    thin text and lab codes on a back don't count."""
    piece = _to_work(crop(img, rect, dpi, inset_mm=3), dpi)
    if piece.size == 0:
        return 0.0
    hsv = cv2.cvtColor(piece, cv2.COLOR_BGR2HSV)
    picture = ((hsv[..., 1] > 50) | (hsv[..., 2] < 170)).astype(np.uint8)
    picture = cv2.morphologyEx(picture, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))  # drop text strokes
    return float(picture.mean())


def find_backs(img, dpi, reference):
    """Prints lying back-side up to the glass, as rects. Empty when every print shows its picture."""
    return [r for r in find_paper(img, dpi, reference) if picture_share(img, r, dpi) < PICTURE_SHARE]


def inside(point, rect):
    return cv2.pointPolygonTest(cv2.boxPoints(rect).astype(np.float32), tuple(map(float, point)), False) >= 0


def check_placement(rects, shape, dpi):
    """Problems a scan would have with these prints: ([short messages], {indices of prints involved})."""
    mm = dpi / 25.4
    boxes = [cv2.boxPoints(r).astype(np.float32) for r in rects]
    edge, close, overlap = [], [], []
    h, w = shape[:2]
    for i, b in enumerate(boxes):
        if (b.min(axis=0) < EDGE_MM * mm).any() or b[:, 0].max() > w - EDGE_MM * mm or b[:, 1].max() > h - EDGE_MM * mm:
            edge.append(i)
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            # nearest corner of one print to the outline of the other (exact for rectangles)
            gap = min(min(-cv2.pointPolygonTest(boxes[j], tuple(map(float, pt)), True) for pt in boxes[i]),
                      min(-cv2.pointPolygonTest(boxes[i], tuple(map(float, pt)), True) for pt in boxes[j]))
            if gap <= 0:
                overlap.append((i, j))
            elif gap < MIN_GAP_MM * mm:
                close.append((i, j))
    problems = []
    if edge:
        problems.append(f"{print_names(edge)} {'reaches' if len(edge) == 1 else 'reach'} the edge of the glass, "
                        "so a strip of it may be cut off")
    if overlap:
        problems.append(_pairs(overlap) + " overlap")
    if close:
        problems.append(_pairs(close) + " are too close (leave ~1 cm)")
    bad = set(edge) | {i for pair in overlap + close for i in pair}
    return problems, bad


def _pairs(pairs):
    return "prints " + ", ".join(f"{i + 1} and {j + 1}" for i, j in pairs)


def print_names(idx):
    nums = [str(i + 1) for i in idx]
    return ("print " if len(nums) == 1 else "prints ") + (", ".join(nums[:-1]) + " and " + nums[-1] if len(nums) > 1 else nums[0])


def hit(rects, x, y):
    """Index of the rect containing bed point (x, y), or None."""
    for i, r in enumerate(rects):
        if cv2.pointPolygonTest(cv2.boxPoints(r).astype(np.float32), (float(x), float(y)), False) >= 0:
            return i
    return None


def _tag(view, text, centre, alert=False):
    """A label centred on a print: light text on a dark, see-through tag, readable on any picture."""
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    pad = 10
    x0, y0 = centre[0] - tw // 2 - pad, centre[1] - th // 2 - pad
    x1, y1 = centre[0] + tw // 2 + pad, centre[1] + th // 2 + base + pad // 2
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, view.shape[1]), min(y1, view.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    area = view[y0:y1, x0:x1]
    colour = np.array((30, 30, 170) if alert else (40, 40, 40), np.float32)
    view[y0:y1, x0:x1] = (area * 0.25 + colour * 0.75).astype(np.uint8)
    cv2.putText(view, text, (centre[0] - tw // 2, centre[1] + th // 2), font, scale, (255, 255, 255), thick,
                cv2.LINE_AA)


TURNED_BG = (43, 43, 43)  # behind a photo shown turned on its side (the app's preview colour)


def _show_turned(glass, view, rect, turn_cw):
    """Draw the print in `rect` (view pixels) on `view` as it will be saved: cut from `glass`, straightened
    and turned `turn_cw` degrees clockwise, as big as fits in the space it takes on the glass."""
    turn = turn_cw % 360
    pic = crop(glass, rect, 100, inset_mm=0)
    if not turn or turn % 90 or pic.size == 0:
        return
    pic = cv2.rotate(pic, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                           270: cv2.ROTATE_90_COUNTERCLOCKWISE}[turn])
    box = cv2.boxPoints(rect).astype(np.int32)
    s = min(rect[1][0] / pic.shape[1], rect[1][1] / pic.shape[0], 1.0)
    pic = cv2.resize(pic, (max(1, int(pic.shape[1] * s)), max(1, int(pic.shape[0] * s))), interpolation=cv2.INTER_AREA)
    cv2.fillPoly(view, [box], TURNED_BG)
    (cx, cy), ph, pw = rect[0], *pic.shape[:2]
    x0, y0 = int(round(cx - pw / 2)), int(round(cy - ph / 2))
    vx0, vy0 = max(x0, 0), max(y0, 0)
    vx1, vy1 = min(x0 + pw, view.shape[1]), min(y0 + ph, view.shape[0])
    if vx1 > vx0 and vy1 > vy0:
        view[vy0:vy1, vx0:vx1] = pic[vy0 - y0:vy1 - y0, vx0 - x0:vx1 - x0]


def draw_overlay(img, rects, labels=None, max_side=1400, alert=(), turns=None):
    """Small preview of the bed with numbered outlines, for the app window (alert: indices in red;
    turns: per rect, degrees clockwise the photo is turned; a turned photo is drawn the way it will look)."""
    f = max_side / max(img.shape[:2])
    view = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    if turns and any(t % 360 for t in turns):
        glass = view.copy()  # cut every print from the untouched bed, even where boxes overlap
        for ((cx, cy), (w, h), a), t in zip(rects, turns):
            _show_turned(glass, view, ((cx * f, cy * f), (w * f, h * f), a), t)
    for i, r in enumerate(rects):
        (cx, cy), (w, h), a = r
        box = cv2.boxPoints(((cx * f, cy * f), (w * f, h * f), a)).astype(np.int32)
        cv2.polylines(view, [box], True, (40, 40, 230) if i in alert else (0, 160, 255), 3)
        _tag(view, labels[i] if labels else str(i + 1), (int(cx * f), int(cy * f)), i in alert)
    return view
