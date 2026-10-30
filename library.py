"""Changes to the photo library besides scanning: turning, splitting, refiling, finding duplicates.

No windows here: the app runs these in the background and shows the message they return, and the
tests run them on a library in a temporary folder. Each takes the catalogue (organizer.Catalog) and,
for long jobs, a Job that reports progress and stops it when Cancel is pressed.
"""
import os
import re
from pathlib import Path

import cv2
from PIL import Image

import datestamp
import detect
import duplicates
import organizer
import rotate
import split


class Cancelled(Exception):
    """The Cancel button was pressed; raised at the job's next safe stopping point."""


class Job:
    """How a job reports progress and learns it was cancelled. This one does nothing (tests, scripts);
    the app's moves its progress bar and listens to its Cancel button."""

    def progress(self, value):
        """value: 0-1 of the whole job."""

    def phase(self, start, end, seconds):
        """The bar creeps from start towards end over the expected time (a scan reports no progress)."""

    def note(self, text):
        """What it's doing now, for the status line."""

    def cancelled(self):
        return False

    def stop_point(self):
        if self.cancelled():
            raise Cancelled


def say(*parts):
    """A status line: the parts given, then what the last organize() couldn't do, if anything."""
    return " ".join(t for t in (*parts, organizer.problem_text()) if t)


def save_image(path, bgr, dpi, fmt, quality):
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if fmt == "TIFF":
        img.save(path, dpi=(dpi, dpi), compression="tiff_lzw")
    else:
        img.save(path, quality=quality, subsampling=0, dpi=(dpi, dpi))


def file_format(path):
    return "TIFF" if Path(path).suffix.lower() in (".tif", ".tiff") else "JPEG"


def inbox(cat):
    """New scans wait here until their back step is done, then get filed."""
    p = cat.root / organizer.INBOX
    p.mkdir(parents=True, exist_ok=True)
    return p


def rel(cat, path):
    return str(Path(path).relative_to(cat.root))


def next_id(cat):
    ids = [int(f.name[:5]) for f in inbox(cat).iterdir() if re.match(r"\d{5}[._]", f.name)]
    return max(max(ids, default=0), cat.max_id()) + 1


def load_photo(cat, pid):
    row = cat.rows("id = ?", (pid,))
    return duplicates.read_image(cat.root / row[0]["path"]) if row and row[0].get("path") else None


def read_text(bgr):
    import ocr  # Windows OCR: loaded only when a back is read
    return ocr.read_text(bgr)


def file_photos(cat, ids):
    """Mark photos ready and run the filing rules; returns a short summary."""
    for pid in ids:
        cat.upsert(pid, status="ready")
    organizer.organize(cat)
    rows = [r for r in cat.rows() if r["id"] in ids]
    places = sorted({str(Path(r["path"]).parent) for r in rows})
    return say("Filed into " + ", ".join(places) if places else "")


def batch_rows(cat, batch):
    return [r for r in cat.rows("status IN ('ready', 'filed')") if organizer.same_batch(r.get("batch"), batch)]


def refile(cat):
    """Apply the naming template (and every filing rule) to the whole library."""
    moved = organizer.organize(cat)
    return say(f"Naming template applied: {moved} photo(s) moved or renamed.")


def turn_photo(cat, pid, delta):
    """Turn a photo a quarter turn: only its EXIF orientation changes, not its pixels. Returns what
    happened, for the status line."""
    rows = cat.rows("id = ?", (pid,))
    if not rows:
        return f"{pid} is no longer in the library."
    row = rows[0]
    if Path(row.get("path") or "").suffix.lower() not in (".jpg", ".jpeg"):
        return f"{pid} is a TIFF: turning in ReCollect works for JPEG photos only. Turn it in your photo app instead."
    turn = (int(row.get("turn") or 0) + delta) % 360
    cat.upsert(pid, turn=turn, meta="")  # meta="": organize() writes the orientation into the file
    if row["status"] in ("ready", "filed"):
        organizer.organize(cat, only={pid})  # waiting for backs: written when it is filed
    return say(f"{pid} turned {turn}° clockwise. Only the orientation tag changes.")


def turn_upright(cat, batch, key, quality, job=Job(), orient_all=rotate.orient_all):
    """Ask Claude which way is up for every photo of a batch, and turn the pixels of those lying wrong."""
    rows = batch_rows(cat, batch)
    turned, problem = 0, None
    for start in range(0, len(rows), 8):  # a few at a time: progress, Cancel, and bounded memory
        job.stop_point()  # photos already turned stay turned (each is complete)
        chunk = rows[start:start + 8]
        job.progress(start / max(1, len(rows)))
        job.note(f"Checking which way is up… {start}/{len(rows)}")
        photos = [duplicates.read_image(cat.root / r["path"], raw=True) for r in chunk]  # as stored, no EXIF turn
        have = [(r, img) for r, img in zip(chunk, photos) if img is not None]
        answers, problem = orient_all(key, [img for _, img in have])
        for (r, img), answer in zip(have, answers):
            if not answer:
                continue
            degrees = answer["turn_clockwise"] if answer["confidence"] != "low" else 0
            if degrees:
                path = cat.root / r["path"]
                save_image(path, rotate.turn(img, degrees), int(r.get("dpi") or 600), file_format(path), quality)
                turned += 1
            total = (int(r.get("rotated") or 0) + degrees) % 360
            fields = {"turn": 0} if degrees else {}  # the pixels are now upright: orientation back to normal
            cat.upsert(r["id"], rotated=total, orientation=f"{answer['confidence']}: {answer['reason']}",
                       meta="", **fields)  # meta="": organize() writes the photo's info into the new file again
    organizer.organize(cat)
    message = f"“{batch}”: {turned} of {len(rows)} photo(s) turned upright."
    if problem:
        message += f" Some couldn't be checked: {problem}"
    return say(message)


def split_photo(cat, row, glass, back, line, quality, job=Job(), read_text=read_text):
    """Split a photo that holds two prints along `line` (split.py), front and back. glass: the front as
    it lay on the glass (its auto-rotate turn undone); back: its back scan or None.
    The first print keeps the photo's number, the second gets the next free one. Returns the message."""
    pid = row["id"]
    dpi, rotated = int(row.get("dpi") or 600), int(row.get("rotated") or 0)
    front_path = cat.root / row["path"]
    photos = [rotate.turn(p, rotated) for p in split.cut(glass, line, dpi)]
    backs = split.cut_back(back, line) if back is not None else (None, None)
    back_dpi = dpi * back.shape[1] / glass.shape[1] if back is not None else dpi  # same spot on the glass
    found = []
    for i, (photo, bk) in enumerate(zip(photos, backs)):  # read everything first: Cancel leaves no trace
        job.stop_point()
        job.progress(0.8 * i / 2)
        size_cm, fmt_name = organizer.print_size(photo.shape[1], photo.shape[0], dpi)
        try:
            stamp = datestamp.read(photo, dpi)
        except Exception:  # only a hint for the year
            stamp = ""
        try:
            prints = duplicates.fingerprints(photo)
        except Exception:
            prints = None
        info = dict(size_cm=size_cm, print_format=fmt_name, date_stamp=stamp, fingerprint=prints,
                    duplicate_of=None, meta=None, description=None)
        if bk is None:  # its back was blank or not scanned (or its file is gone)
            status = row.get("back_status")
            info.update(back_status="not scanned" if status == "saved" else status, back_path="")
        elif detect.is_blank(bk, back_dpi):
            info.update(back_status="blank", back_path="", back_text="", back_lab="", paper="")
        else:
            job.note(f"Reading the back of print {i + 1}…")
            personal, lab, paper = organizer.split_back_text(read_text(bk))
            info.update(back_status="saved", back_text=personal, back_lab=lab, paper=paper)
        found.append(info)
    job.stop_point()
    job.progress(0.8)
    new_id = f"{next_id(cat):05d}"
    out = inbox(cat)
    # both prints start again from the inbox and are filed by the naming template like new scans;
    # the first keeps the photo's number, and the old files go to the Recycle Bin
    old = [front_path] + ([cat.root / row["back_path"]] if back is not None else [])
    new = []  # (temporary name, final name): an old file may still sit in the inbox under the final name
    for pid_i, photo, bk, info in zip((pid, new_id), photos, backs, found):
        path = out / f"{pid_i}{front_path.suffix.lower()}"
        new.append((path.with_suffix(".split" + path.suffix), path))
        save_image(new[-1][0], photo, dpi, file_format(path), quality)
        info["path"] = rel(cat, path)
        if info["back_status"] == "saved":
            path = out / f"{pid_i}_back{old[1].suffix.lower()}"
            new.append((path.with_suffix(".split" + path.suffix), path))
            save_image(new[-1][0], bk, round(back_dpi), file_format(path), quality)
            info["back_path"] = rel(cat, path)
    for f in old:
        organizer.recycle(f)
    for tmp, path in new:
        os.replace(tmp, path)
    cat.upsert(pid, status="ready", **found[0])
    same = {k: row.get(k) for k in ("batch", "scanned", "bed", "dpi", "year_manual", "rotated", "orientation",
                                    "turn")}
    cat.upsert(new_id, status="ready", **same, **found[1])
    organizer.organize(cat)
    if back is None:
        backs_note = "No back was stored for it (blank or not scanned), so only the front was split."
    else:
        kept = [p for p, info in ((pid, found[0]), (new_id, found[1])) if info["back_status"] == "saved"]
        backs_note = ("Both backs kept." if len(kept) == 2 else
                      f"Back kept for {kept[0]}; the other was blank." if kept else "Both backs were blank.")
    return say(f"{pid} split into {pid} and {new_id}. {backs_note}")


def find_duplicates(cat, job=Job()):
    """(pairs of (duplicate id, original id), message). Tags new duplicates in the catalogue and files."""
    rows = cat.rows("status IN ('ready', 'filed')")
    for i, r in enumerate(rows):  # photos scanned before this feature have no fingerprint yet
        job.stop_point()  # fingerprints already stored are kept: they are only a cache
        if i % 10 == 0:
            job.progress(0.5 * i / max(1, len(rows)))
        if not r.get("fingerprint"):
            if i % 10 == 0:
                job.note(f"Fingerprinting older photos… {i}/{len(rows)}")
            img = duplicates.read_image(cat.root / r["path"])
            if img is not None:
                r["fingerprint"] = duplicates.fingerprints(img)
                cat.upsert(r["id"], fingerprint=r["fingerprint"])

    def progress(i, n):
        job.stop_point()
        job.progress(0.5 + 0.5 * i / max(1, n))
        job.note(f"Comparing photos… {i}/{n}")
    pairs = duplicates.find_all(rows, lambda pid: load_photo(cat, pid), progress)
    job.stop_point()
    changed = False
    for dup, orig, _ in pairs:
        row = next(r for r in rows if r["id"] == dup)
        if row.get("duplicate_of") != orig:
            cat.upsert(dup, duplicate_of=orig, meta=None)  # tag it Duplicate in the file too
            changed = True
    if changed:
        organizer.organize(cat)
    if not pairs:
        return [], f"No duplicates among {len(rows)} photos."
    return [(d, o) for d, o, _ in pairs], f"Found {len(pairs)} duplicate(s) among {len(rows)} photos."
