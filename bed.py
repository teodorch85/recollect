"""What happens to a scan of the bed: the fronts are cut out and saved, then the backs are matched to
them and read, and a preview checks the placement. The app drives the scanner and hands the scanned
image to these; the tests hand them made-up beds.

A bed is the list of prints on the glass waiting for their backs: [{id, rect, dpi, dup}].
"""
import time
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

import datestamp
import detect
import duplicates
import enhance
import library
import organizer
import rotate

PREVIEW_DPI = 150  # quick look at the bed: about 10 s, enough to find the prints
BACK_MARGIN_IN = 0.4  # around the prints when scanning backs: room for prints that shifted when flipped


def scan_seconds(dpi, share=1.0):
    """Rough V600 time for a pass over `share` of the bed (it reports no progress of its own):
    about 15 s up to 300 dpi, 65 s at 600 dpi."""
    return max(8.0, (15 + max(0, dpi - 300) / 300 * 50) * share)


def scale_rect(rect, from_dpi, to_dpi):
    f = to_dpi / from_dpi
    (cx, cy), (w, h), a = rect
    return (cx * f, cy * f), (w * f, h * f), a


def ext(s):
    return ".tif" if s["format"] == "TIFF" else ".jpg"


@dataclass
class Fronts:
    view: object                                # the bed with the prints outlined
    message: str
    bed: list = field(default_factory=list)     # prints now waiting for their backs ([] once filed)
    shown: tuple = None                         # (rects, labels, red ones, ids) of the fronts saved


def drop(cat, bed):
    """Forget a bed's fronts: their files in the inbox and their catalogue rows."""
    out = library.inbox(cat)
    for p in bed:
        for f in out.glob(f"{p['id']}*"):
            f.unlink()
        cat.delete(p["id"])


def _duplicate_check(cat, photo, known):
    """(fingerprints of this front, id of the library photo it repeats or None)."""
    try:
        prints = duplicates.fingerprints(photo)
        match = duplicates.best_match(photo, prints, known, lambda pid: library.load_photo(cat, pid))
    except Exception:  # a hint only: never lose a scan over it
        return None, None
    if not match:
        return prints, None
    row = cat.rows("id = ?", (match[0],))
    return prints, (row[0].get("duplicate_of") or match[0]) if row else match[0]


def no_answers(photos):
    return [None] * len(photos), None


def save_fronts(cat, img, dpi, reference, s, batch, scan_backs, orient=no_answers, job=library.Job(),
                started=None):
    """Cut the prints out of a bed scan, save and catalogue them. With scan_backs they wait for their
    backs (Fronts.bed); otherwise they are filed at once. orient(photos) -> (answers, problem): which
    way is up (rotate.orient_all), or no_answers."""
    started = started or time.time()
    backs = detect.find_backs(img, dpi, reference)
    if backs:  # saving these would file blank paper as photos
        return Fronts(detect.draw_overlay(img, backs, alert=range(len(backs))),
                      f"Nothing saved: {len(backs)} print{'s are' if len(backs) > 1 else ' is'} lying back-side "
                      "down (red). Turn them face-down and press Space again.")
    rects = detect.find_photos(img, dpi, reference)
    if not rects:
        return Fronts(detect.draw_overlay(img, []), "No prints found on the glass. Check placement and try again.")
    out, first = library.inbox(cat), library.next_id(cat)
    scanned = datetime.now().isoformat(timespec="seconds")
    bed, stamps = [], 0
    known = [(r["id"], r["fingerprint"]) for r in cat.rows("fingerprint IS NOT NULL AND fingerprint != ''")]
    photos = []
    for i, r in enumerate(rects):
        job.stop_point()  # nothing saved yet
        job.progress(0.6 + 0.15 * i / len(rects))
        photos.append(enhance.process_front(detect.crop(img, r, dpi), dpi, s["sharpen"], s["restore_color"]))
    turns, turn_problem = orient(photos)
    n_turned = 0
    job.stop_point()
    for i, (r, photo, answer) in enumerate(zip(rects, photos, turns)):
        if job.cancelled():
            drop(cat, bed)  # undo the fronts of this bed saved so far
            raise library.Cancelled
        job.progress(0.8 + 0.2 * i / len(rects))
        pid = f"{first + i:05d}"
        turned = answer["turn_clockwise"] if answer and answer["confidence"] != "low" else 0
        n_turned += bool(turned)
        photo = rotate.turn(photo, turned)
        path = out / f"{pid}{ext(s)}"
        library.save_image(path, photo, dpi, s["format"], s["jpeg_quality"])
        size_cm, fmt = organizer.print_size(photo.shape[1], photo.shape[0], dpi)
        try:
            stamp = datestamp.read(photo, dpi)
        except Exception:  # only a hint for the year: never lose a scan over it
            stamp = ""
        stamps += bool(stamp)
        prints, dup = _duplicate_check(cat, photo, known)
        cat.upsert(pid, status="scanning", batch=batch, scanned=scanned,
                   bed=f"{first:05d}", dpi=dpi, size_cm=size_cm, print_format=fmt, date_stamp=stamp,
                   back_status="pending", path=library.rel(cat, path), back_path="",
                   fingerprint=prints, duplicate_of=dup, rotated=turned,
                   orientation=f"{answer['confidence']}: {answer['reason']}" if answer else "")
        known.append((pid, prints))
        bed.append({"id": pid, "rect": r, "dpi": dpi, "dup": dup})
    if job.cancelled():  # pressed while the last photo was being saved
        drop(cat, bed)
        raise library.Cancelled
    labels = [p["id"] + (f" = {p['dup']}" if p["dup"] else "") for p in bed]
    dups = [i for i, p in enumerate(bed) if p["dup"]]
    view = detect.draw_overlay(img, rects, labels, alert=dups)
    shown = (rects, labels, dups, [p["id"] for p in bed])
    tip = "Click a photo to turn it (the arrow shows its top). "
    took = f"{time.time() - started:.0f}s" + (f", {stamps} with a camera date" if stamps else "")
    if n_turned:
        took += f", {n_turned} turned upright"
    if turn_problem:
        took += f"; not turned: {turn_problem}"
    warn = ""
    if dups:
        warn = (f"⚠ {len(dups)} scanned before (red, “= original”): "
                + ("press “Remove duplicates” to drop them. " if scan_backs else
                   "kept and tagged Duplicate; review them with Find duplicates. "))
    if scan_backs:
        return Fronts(view, (f"Saved {len(rects)} fronts ({took}). {warn}Check the outlines: one box per print? "
                             f"{tip}Then flip each print IN THE SAME SPOT and press Space.  (N = skip backs, "
                             "Redo fronts = boxes wrong)"), bed, shown)
    for p in bed:
        cat.upsert(p["id"], back_status="not scanned")
    summary = library.file_photos(cat, [p["id"] for p in bed])
    return Fronts(view, (f"Saved {len(rects)} photos ({took}). {warn}{summary}. {tip}Place the next prints and "
                         "press Space."), [], shown)


def back_region(bed, dpi):
    """The part of the glass to scan for the backs, in inches (x, y, width, height). Backs are only read
    for text, so just the strip the prints occupy: head travel, not resolution, is what costs time."""
    pts = np.vstack([cv2.boxPoints(scale_rect(p["rect"], p["dpi"], dpi)) for p in bed]) / dpi
    x0, y0 = pts.min(axis=0) - BACK_MARGIN_IN
    x1, y1 = pts.max(axis=0) + BACK_MARGIN_IN
    return max(x0, 0), max(y0, 0), x1 - max(x0, 0), y1 - max(y0, 0)


def save_backs(cat, img, dpi, bed, reference, s, job=library.Job(), read_text=library.read_text):
    """Match each print of the bed with its back in a scan of the flipped prints, save and read the
    backs that aren't blank, then file the bed. Returns (view, message). Cancelled: nothing is kept
    and the bed still waits for its backs."""
    front_rects = [scale_rect(p["rect"], p["dpi"], dpi) for p in bed]
    found = detect.find_photos(img, dpi, reference, refine=False)
    backs = detect.pair_backs(front_rects, found, dpi)
    out = library.inbox(cat)
    labels, kept, rows, saved = [], 0, [], []
    try:
        for i, (p, r) in enumerate(zip(bed, backs)):
            job.stop_point()
            job.progress(0.4 + 0.55 * i / len(backs))
            back = detect.crop(img, r, dpi)
            if detect.is_blank(back, dpi):
                rows.append((p["id"], {"back_status": "blank"}))
                labels.append(f"{p['id']} blank")
                continue
            path = out / f"{p['id']}_back{ext(s)}"
            library.save_image(path, back, dpi, s["format"], s["jpeg_quality"])
            saved.append(path)
            job.note(f"Reading back of {p['id']}…")
            personal, lab, paper = organizer.split_back_text(read_text(back))
            rows.append((p["id"], {"back_status": "saved", "back_path": library.rel(cat, path),
                                   "back_text": personal, "back_lab": lab, "paper": paper}))
            labels.append(f"{p['id']} " + ("text" if personal else "back"))
            kept += 1
        job.stop_point()
    except library.Cancelled:  # the bed stays waiting for its backs, with nothing of this pass kept
        for path in saved:
            path.unlink(missing_ok=True)
        raise
    for pid, fields in rows:  # written only once the whole bed is read
        cat.upsert(pid, **fields)
    summary = library.file_photos(cat, [p["id"] for p in bed])
    return (detect.draw_overlay(img, backs, labels),
            f"Backs: {kept} kept, {len(bed) - kept} blank. {summary}. Place the next prints and press Space.")


def prints_on_glass(img, dpi, reference):
    """(rects of every print, indices of the ones lying back-side down)."""
    backs = detect.find_backs(img, dpi, reference)
    # the picture detector boxes watermark text on white backs: let the paper outlines replace it
    photos = [r for r in detect.find_photos(img, dpi, reference, refine=False)
              if not any(detect.inside(r[0], b) for b in backs)]
    rects = detect.reading_order(photos + backs, dpi)
    return rects, {i for i, r in enumerate(rects) if r in backs}


def check_placement(img, reference, dpi=PREVIEW_DPI):
    """(view, message) for a quick preview scan: how many prints, and whether they lie well."""
    rects, backs = prints_on_glass(img, dpi, reference)
    found = "Preview only, nothing saved. "
    if not rects:
        return detect.draw_overlay(img, []), found + "No prints found on the glass."
    found += f"Found {len(rects)} print{'s' if len(rects) > 1 else ''}."
    problems, bad = detect.check_placement(rects, img.shape, dpi)
    if backs:
        problems.insert(0, f"{detect.print_names(sorted(backs))} {'is' if len(backs) == 1 else 'are'} "
                           "lying back-side down: turn face-down")
    view = detect.draw_overlay(img, rects, alert=bad | backs)
    if not problems:
        return view, f"{found}\n✓ Placement looks good. If that's the right number, press Space to scan."
    bullets = "\n".join(f"⚠ {p[0].upper()}{p[1:]}." for p in problems)
    big = max(max(r[1]) for r in rects) / dpi * 2.54 >= 19  # 15x20 cm or larger
    tip = "\nTip: 15×20 prints fit only one per scan." if big and len(rects) > 1 and bad else ""
    then = ("Turn them over and press P to check again." if backs else
            "Move them and press P to check again, or press Space to scan anyway.")
    return view, f"{found}\n{bullets}{tip}\n{then}"
