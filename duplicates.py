"""Spot prints that were scanned before (the same print twice, or a reprint of it).

Each front gets a 64-bit fingerprint (pHash: the coarse light/dark pattern of the picture), which
survives a slightly different crop, tilt, size, sharpening or colour. Fingerprints that are close
are then confirmed by finding the middle of one print inside the other (small greyscale thumbnails,
a few sizes, so a different crop or a reprint with other borders still matches), which keeps two
different frames of the same scene (e.g. two group photos at the same door) apart.
A print may lie turned on the glass the second time, so all four turns are compared.
"""
import cv2
import numpy as np

MAX_BITS = 20       # fingerprints differing in at most this many of 64 bits are candidates
MIN_SIMILAR = 0.85  # how well the middle of one print must match the other to call it a duplicate
THUMB = 64
SCALES = (0.9, 0.95, 1.0, 1.05, 1.1)  # the other print's crop may be a little tighter or looser


def _turns(bgr):
    return [bgr, cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(bgr, cv2.ROTATE_180),
            cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)]


def _gray(bgr, size):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    small = cv2.resize(g, (size * 4, size * 4), interpolation=cv2.INTER_AREA)  # two steps: less aliasing
    return cv2.resize(small, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)


def fingerprint(bgr):
    """16 hex characters (pHash) of a photo as it is."""
    low = cv2.dct(_gray(bgr, 32))[:8, :8].flatten()[1:]  # lowest frequencies, without the average
    bits = np.concatenate(([0], low > np.median(low)))
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def thumbnail(bgr):
    """Small greyscale picture for the confirmation step (contrast evened out: faded vs restored)."""
    return cv2.equalizeHist(_gray(bgr, THUMB).astype(np.uint8))


def distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def similarity(t1, t2):
    """How well the middle 76% of t1 is found in t2 at some size and shift (1.0 = identical)."""
    m = int(THUMB * 0.12)
    middle = t1[m:THUMB - m, m:THUMB - m]
    best = -1.0
    for f in SCALES:
        other = cv2.resize(t2, (round(THUMB * f), round(THUMB * f)), interpolation=cv2.INTER_AREA)
        if other.shape[0] >= middle.shape[0]:
            best = max(best, float(cv2.matchTemplate(other, middle, cv2.TM_CCOEFF_NORMED).max()))
    return best


def fingerprints(bgr):
    """Fingerprints of the photo in its four turns, as stored in the catalogue ("a b c d")."""
    return " ".join(fingerprint(t) for t in _turns(bgr))


def read_image(path):
    """cv2.imread that also opens Greek file names."""
    try:
        return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def _confirm(turn_thumbs, other):
    return max(similarity(t, thumbnail(other)) for t in turn_thumbs) if other is not None else -1.0


def best_match(bgr, prints, known, load):
    """The known photo this one repeats, as (id, similarity), or None.

    prints: fingerprints(bgr); known: [(id, its fingerprints)]; load(id) -> that photo (BGR) or None.
    """
    mine = prints.split()
    candidates = sorted((min(distance(p, fp.split()[0]) for p in mine), pid) for pid, fp in known if fp)
    thumbs = None
    for bits, pid in candidates:
        if bits > MAX_BITS:
            break
        thumbs = thumbs or [thumbnail(t) for t in _turns(bgr)]
        sim = _confirm(thumbs, load(pid))
        if sim >= MIN_SIMILAR:
            return pid, sim
    return None


def _as_ints(prints):
    return np.array([int(p, 16) for p in prints.split()], dtype=np.uint64)


def find_all(rows, load, progress=None):
    """Every photo that repeats an earlier one: [(id, id of the earlier photo, similarity)].

    rows: catalogue rows with a fingerprint, oldest first. Pairs listed in distinct_from are skipped.
    """
    rows = [r for r in rows if r.get("fingerprint")]
    turns = np.stack([_as_ints(r["fingerprint"]) for r in rows]) if rows else np.zeros((0, 4), np.uint64)
    first = turns[:, 0] if len(rows) else turns
    found, thumbs = [], {}
    for i, r in enumerate(rows):
        if progress and i % 50 == 0:
            progress(i, len(rows))
        if not i:
            continue
        x = turns[i][:, None] ^ first[None, :i]  # 4 turns x every earlier photo
        bits = np.unpackbits(x.view(np.uint8).reshape(4, i, 8), axis=2).sum(axis=2).min(axis=0)
        distinct = set((r.get("distinct_from") or "").split())
        for j in np.argsort(bits, kind="stable"):
            if bits[j] > MAX_BITS:
                break
            other = rows[j]["id"]
            if other in distinct:
                continue
            if r["id"] not in thumbs:
                img = load(r["id"])
                thumbs[r["id"]] = [thumbnail(t) for t in _turns(img)] if img is not None else []
            if not thumbs[r["id"]]:
                break
            sim = _confirm(thumbs[r["id"]], load(other))
            if sim >= MIN_SIMILAR:
                found.append((r["id"], other, sim))
                break
    return found
