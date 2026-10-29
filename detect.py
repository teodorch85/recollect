"""Find individual prints on a bed scan, straighten/crop them, and pair fronts with backs."""
import cv2
import numpy as np

WORK_DPI = 100          # detection runs on a downscaled copy at this resolution
GAP_DPI = 200           # finer copy used to find narrow gaps between prints that nearly touch
MIN_SIDE_IN = 1.0       # ignore anything smaller than 1 inch on a side
INSET_MM = 0.6          # shave this much off each edge so no lid shows at the border


def _to_work(img, dpi):
    f = WORK_DPI / dpi
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)


def _gray_blur(img):
    return cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def find_photos(img, dpi, reference=None, refine=True):
    """Return rotated rects ((cx, cy), (w, h), angle) in full-resolution pixels.

    `reference` is an empty-bed scan (any dpi); subtracting it makes white
    borders and white photo backs stand out from the white lid. Pass
    refine=False for backs: plain white paper looks like lid to the edge snapper.
    """
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
    contours = _split_touching(contours, filled.shape, _gap_lines(img, dpi))

    rects = _rects(contours, dpi)
    return reading_order([_refine(img, r, dpi, lid) for r in rects] if refine else rects, dpi)


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


def _gap_lines(img, dpi):
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
    thin = ((tophat > 20) & (g > 190)).astype(np.uint8) * 255
    segs = cv2.HoughLinesP(thin, 1, np.pi / 360, threshold=int(0.8 * GAP_DPI),
                           minLineLength=int(1.5 * GAP_DPI), maxLineGap=int(0.05 * GAP_DPI))
    lines = []
    for x1, y1, x2, y2 in (segs.reshape(-1, 4) if segs is not None else []):
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 90
        if min(ang, 90 - ang) > 8:
            continue
        # stretch each end by 0.3" so the cut reaches the blob's outer edge
        d = np.array([x2 - x1, y2 - y1], float)
        d *= 0.3 * GAP_DPI / np.linalg.norm(d)
        p1 = (np.array([x1, y1]) - d) * WORK_DPI / GAP_DPI
        p2 = (np.array([x2, y2]) + d) * WORK_DPI / GAP_DPI
        lines.append((tuple(p1.astype(int)), tuple(p2.astype(int))))
    return lines


def _is_print_shaped(c, min_side):
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    return min(w, h) >= min_side and cv2.contourArea(c) >= 0.9 * w * h


def _split_touching(contours, shape, lines):
    """Cut blobs along gap lines: separates prints that nearly touch and trims lid strips off edges.

    A cut is kept only when it leaves clean print-sized rectangles plus, at most, thin
    slivers of lid; that stops a white line *inside* a picture (horizon, door frame)
    from splitting it.
    """
    if not lines:
        return contours
    min_side = 1.5 * WORK_DPI
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

    def lid_like(strip, axis):
        mean, std = strip.mean(axis=axis), strip.std(axis=axis)
        return (mean > lid - 25) & (mean < lid + 4) & (std < 7)

    rot = cv2.getRotationMatrix2D((0, 0), a, 1.0)[:, :2]
    inv = np.linalg.inv(rot)

    def outside(offsets, axis):  # lines beyond the scanned area (print pushed against the bed edge)
        pts = np.zeros((len(offsets), 2))
        pts[:, axis] = offsets
        xy = pts @ inv.T + (cx, cy)
        return (xy[:, 0] < 0) | (xy[:, 0] >= img.shape[1]) | (xy[:, 1] < 0) | (xy[:, 1] >= img.shape[0])

    cols = lid_like(g[core_r, :], 0) | outside(np.arange(W) - W / 2 + 0.5, 0)
    rows = lid_like(g[:, core_c], 1) | outside(np.arange(H) - H / 2 + 0.5, 1)

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
        problems.append(f"{print_names(edge)} {'touches' if len(edge) == 1 else 'touch'} the edge of the glass")
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


def draw_overlay(img, rects, labels=None, max_side=1400, alert=()):
    """Small preview of the bed with numbered outlines, for the app window (alert: indices in red)."""
    f = max_side / max(img.shape[:2])
    view = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    for i, r in enumerate(rects):
        (cx, cy), (w, h), a = r
        box = cv2.boxPoints(((cx * f, cy * f), (w * f, h * f), a)).astype(np.int32)
        cv2.polylines(view, [box], True, (40, 40, 230) if i in alert else (0, 160, 255), 3)
        text = labels[i] if labels else str(i + 1)
        org = (int(cx * f) - 20, int(cy * f) + 12)
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 200, 255), 2, cv2.LINE_AA)
    return view
