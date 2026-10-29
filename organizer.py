"""Catalogue and file scanned prints with simple rules (no AI).

Layout of the photos folder:
    photos.db, catalog.csv        everything known about every photo (catalog.csv opens in Excel)
    <YEAR>/<Batch>/               only the photos themselves
    _backs/                       back scans, same name + _back (kept as evidence, out of the way)
    _inbox/                       scans whose back step isn't finished yet

Year of a photo, first rule that gives one:
    0. a year you set for its batch in the app ("Set year")
    1. a year written/printed on its own back (OCR)
    2. a year in the batch label typed while scanning (e.g. "1994 Crete")
    3. the year found on the back of another print of the same batch scanned the same day
    4. the orange date the camera printed into the picture (datestamp.py)
    5. the camera date found on other prints of the same batch
    6. otherwise "Unknown year"
Camera dates come after everything people wrote, because a camera whose clock was never set
prints a wrong date, and one such print must not pull it away from the rest of its batch.

Each photo also carries this in its own metadata (metadata.py), in the tags Immich reads: the back
text as description, tags like Batch/Crete and Dated by/back, and the date when the year is known.
"""
import csv
import json
import os
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

import metadata

INBOX, BACKS, UNKNOWN = "_inbox", "_backs", "Unknown year"

# status: scanning (back step not done) -> ready -> filed
FIELDS = ["id", "status", "batch", "scanned", "bed", "dpi", "size_cm", "print_format", "back_status",
          "back_text", "back_lab", "paper", "date_stamp", "year_manual", "year", "year_source", "path",
          "back_path", "organized", "meta", "description", "fingerprint", "duplicate_of", "distinct_from"]
# description = what organize() last wrote as the photo's description (so it knows what it may clear)
# fingerprint = duplicates.fingerprints() of the front; duplicate_of = id of the photo it repeats;
# distinct_from = ids you marked "not a duplicate" of this one (space separated)
META = "4"  # bump when the metadata written into photos changes, so organize() rewrites it everywhere

# Common print sizes in cm; the format hints at the era (e.g. 9x13 in the 70s-80s, 10x15 from the 90s).
PRINT_FORMATS = [(9, 9), (9, 13), (10, 10), (10, 15), (11, 15), (13, 13), (13, 18), (15, 20), (15, 21),
                 (18, 24), (20, 25), (20, 30), (6, 9), (7, 10), (8, 11)]

# keyword found in the OCR text -> paper name (the watermark is faint, so often only one word survives)
PAPERS = [("endura", "Kodak Endura"), ("royal", "Kodak Royal"), ("kodak edge", "Kodak Edge"), ("kodak", "Kodak"),
          ("crystal", "Fuji Crystal Archive"), ("fuji", "Fuji"), ("agfa", "Agfa"), ("konica", "Konica"),
          ("ilford", "Ilford"), ("mitsubishi", "Mitsubishi")]
# watermark words printed all over lab paper; not useful in a filename
BOILERPLATE = re.compile(r"kodak|endura|fuji|crystal|archive|agfa|konica|paper|papier|papel|copyright|"
                         r"professional|protected|reproduc|interdit|gestattet|geschütz|prohibid|copy",
                         re.IGNORECASE)
# machine lines a minilab prints (order numbers, exposure codes, digital file names)
LAB_LINE = re.compile(r"PROFILE|\.JPG|\bJPG\b|\d{3,}\s+\d{3}|\bNN\b|\d+\.\d\s*/", re.IGNORECASE)
YEAR = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-4][0-9])(?!\d)")
DATE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)")
SHORT_YEAR = re.compile(r"['’`](\d{2})(?!\d)")  # '94


def slug(text, n=40):
    text = re.sub(r"(?<=\d)[./](?=\d)", "-", text or "")  # 15/7/1994 -> 15-7-1994
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    return re.sub(r"[\s_]+", "-", text)[:n].strip("-").lower()


def folder_name(text):
    return re.sub(r'[<>:"/\\|?*]', "", text or "").strip()[:60]


def print_size(w_px, h_px, dpi):
    """('20.3x15.0', '15x20') — measured size in cm and the nearest standard print format."""
    a, b = sorted((w_px / dpi * 2.54, h_px / dpi * 2.54))
    best = min(PRINT_FORMATS, key=lambda f: abs(f[0] - a) + abs(f[1] - b))
    fmt = f"{best[0]}x{best[1]}" if abs(best[0] - a) < 1.0 and abs(best[1] - b) < 1.0 else ""
    return f"{max(a, b):.1f}x{min(a, b):.1f}", fmt


def split_back_text(text):
    """OCR text of a back -> (personal text, lab machine lines, paper brand)."""
    paper = next((name for key, name in PAPERS if key in text.lower()), "")
    personal, lab = [], []
    for line in text.splitlines():
        if LAB_LINE.search(line):
            lab.append(line)
        elif not BOILERPLATE.search(line) and _looks_like_text(line):
            personal.append(line)
    return "\n".join(personal), "\n".join(lab), paper


CLEAN_TOKEN = re.compile(r"^([A-ZΑ-Ω]?[a-zα-ωά-ώϊϋΐΰ]+|[A-ZΑ-ΩΆ-Ώ]+|\d+([./-]\d+)*)[.,:;!?]?$")


def _looks_like_text(line):
    """Reject OCR noise from watermarks (e.g. 'E frJC)IJRA'): most tokens must be real words or numbers."""
    tokens = [t for t in line.split() if len(t) >= 2]
    return bool(tokens) and sum(bool(CLEAN_TOKEN.match(t)) for t in tokens) >= 0.7 * len(tokens)


def find_date(text, this_year=None):
    """A full day-month-year date in free text (15/7/94 -> '1994-07-15'), or None."""
    this_year = this_year or datetime.now().year
    for m in DATE.finditer(text or ""):
        y = full_year(int(m.group(3)), this_year)
        try:
            if 1900 <= y <= this_year:
                return datetime(y, int(m.group(2)), int(m.group(1))).date().isoformat()
        except ValueError:
            pass
    return None


def find_year(text, this_year=None):
    """A plausible year in free text: dates like 15/7/94, years like 1994, or '94."""
    this_year = this_year or datetime.now().year
    full = find_date(text, this_year)
    if full:
        return int(full[:4])
    for m in YEAR.finditer(text):
        if 1900 <= int(m.group(1)) <= this_year:
            return int(m.group(1))
    m = SHORT_YEAR.search(text)
    return full_year(int(m.group(1)), this_year) if m else None


def full_year(y, this_year=None):
    """94 -> 1994, 05 -> 2005 (two-digit years up to this year's are this century); 1994 stays."""
    this_year = this_year or datetime.now().year
    return y + (1900 if y > this_year % 100 else 2000) if y < 100 else y


class Catalog:
    """SQLite store of everything known about each photo (thread-safe)."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "photos.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY)")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(photos)")}
        for f in FIELDS:
            if f not in have:  # databases from earlier versions gain new columns
                self.db.execute(f"ALTER TABLE photos ADD COLUMN {f} TEXT")
        self.db.commit()

    def upsert(self, pid, **fields):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO photos (id) VALUES (?)", (pid,))
            for k, v in fields.items():
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                self.db.execute(f"UPDATE photos SET {k} = ? WHERE id = ?", (None if v is None else str(v), pid))
            self.db.commit()

    def delete(self, pid):
        with self.lock:
            self.db.execute("DELETE FROM photos WHERE id = ?", (pid,))
            self.db.commit()

    def rows(self, where="1", args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(f"SELECT * FROM photos WHERE {where} ORDER BY id", args)]

    def max_id(self):
        with self.lock:
            row = self.db.execute("SELECT MAX(CAST(id AS INTEGER)) FROM photos").fetchone()
        return int(row[0] or 0)

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]

    def export_csv(self):
        with self.lock:
            rows = self.db.execute(f"SELECT {', '.join(FIELDS)} FROM photos ORDER BY id").fetchall()
        tmp = self.root / "catalog.csv.tmp"
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Greek correctly
            w = csv.writer(f)
            w.writerow(FIELDS)
            w.writerows([tuple(r) for r in rows])
        try:
            tmp.replace(self.root / "catalog.csv")
        except PermissionError:  # catalog.csv is open in Excel; it is rewritten next time
            tmp.unlink(missing_ok=True)


def _write_metadata(path, row, year, source):
    # the most exact date that agrees with the chosen year
    dates = [find_date(row.get("back_text")), row.get("date_stamp"), str(year) if year else None]
    date = next((d for d in dates if d and year and d.startswith(str(year))), None)
    values = {"Batch": row.get("batch"), "Paper": row.get("paper"), "Print size": row.get("print_format")}
    tags = [f"{k}/{v.replace('/', '-')}" for k, v in values.items() if v]
    keywords = [v for v in values.values() if v]  # flat list for apps without tag folders
    tags.append(f"Dated by/{source}" if year else "Undated")
    keywords.append(f"dated by {source}" if year else "Undated")
    if row.get("back_text"):
        tags.append("Back text")
        keywords.append("Back text")
    if row.get("duplicate_of"):
        tags.append("Duplicate")
        keywords.append("Duplicate")
    description = row.get("back_text") or ""
    metadata.write(path, description, tags, keywords, date, replaces=row.get("description"))
    return description


def _years_by_group(rows, year_of):
    """Most common year that year_of(row) finds, per (batch, scan day) group."""
    found = {}
    for r in rows:
        y = year_of(r)
        if y:
            found.setdefault(_group(r), []).append(y)
    return {g: Counter(ys).most_common(1)[0][0] for g, ys in found.items()}


def _written_year(r):
    return find_year(r.get("back_text") or "") or find_year(r.get("batch") or "")


def _group(r):
    # same label on the same scanning day = same pile of prints; without a label, the same bed
    return (r.get("batch") or f"bed {r.get('bed')}", (r.get("scanned") or "")[:10])


def _stamp_year(r):
    return int(r["date_stamp"][:4]) if (r.get("date_stamp") or "")[:4].isdigit() else None


def _decide_year(r, written, stamped):
    """(year, source) by the rules at the top; written/stamped: group -> year from _years_by_group."""
    if (r.get("year_manual") or "").isdigit():
        return int(r["year_manual"]), "set by you"
    for source, y in (("back", find_year(r.get("back_text") or "")), ("batch label", find_year(r.get("batch") or "")),
                      ("same batch", written.get(_group(r))), ("date stamp", _stamp_year(r)),
                      ("same batch", stamped.get(_group(r)))):
        if y:
            return y, source
    return None, ""


def batches(catalog):
    with catalog.lock:
        return [r[0] for r in catalog.db.execute(
            "SELECT DISTINCT batch FROM photos WHERE batch IS NOT NULL AND batch != '' ORDER BY batch")]


def set_batch_year(catalog, batch, year):
    """Give every photo of a batch a year (empty year = back to the automatic rules)."""
    with catalog.lock:
        catalog.db.execute("UPDATE photos SET year_manual = ? WHERE batch = ?", (str(year or ""), batch))
        catalog.db.commit()
    return organize(catalog)


def organize(catalog):
    """(Re)file every ready photo by the rules above. Cheap: only moves files whose place changed.
    Returns the number of photos moved."""
    root = catalog.root
    rows = catalog.rows("status IN ('ready', 'filed')")
    written, stamped = _years_by_group(rows, _written_year), _years_by_group(rows, _stamp_year)
    moved = changed = 0
    for r in rows:
        year, source = _decide_year(r, written, stamped)
        # the year already names the parent folder: "1988 Crete" -> 1988/Crete
        batch = folder_name(re.sub(rf"\b{year}\b", "", r.get("batch") or "") if year else r.get("batch"))
        batch = re.sub(r"\s{2,}", " ", batch).strip(" -_") or "Unsorted"
        dest = root / (str(year) if year else UNKNOWN) / batch
        snippet = slug(r.get("back_text") or "", 40)
        base = "_".join(p for p in (str(year) if year else "undated", slug(batch, 30), r["id"], snippet) if p)
        front = root / r["path"]
        new_front = dest / f"{base}{front.suffix.lower()}"
        back = root / r["back_path"] if r.get("back_path") else None
        new_back = root / BACKS / f"{base}_back{back.suffix.lower()}" if back else None
        unchanged = (new_front == front and new_back == back and r["status"] == "filed" and r.get("meta") == META
                     and str(year or "") == (r.get("year") or "") and source == (r.get("year_source") or ""))
        if unchanged:
            continue
        if not front.exists():
            continue  # moved or deleted by hand; leave the row alone
        dest.mkdir(parents=True, exist_ok=True)
        cur_front, cur_back = front, back
        try:  # os.rename: same drive, instant, and fails cleanly (no half copy) if a file is open
            r["description"] = _write_metadata(front, r, year, source)
            if new_front != front:
                os.rename(front, new_front)
                cur_front = new_front
                _remove_empty(front.parent, root)
            if back and back.exists() and new_back != back:
                new_back.parent.mkdir(exist_ok=True)
                os.rename(back, new_back)
                cur_back = new_back
        except (OSError, RuntimeError):  # RuntimeError: exiv2 couldn't parse the file
            pass  # e.g. open in a viewer: what didn't move is retried on the next run
        done = cur_front == new_front and cur_back == new_back
        catalog.upsert(r["id"], status="filed" if done else r["status"],
                       year=str(year or "") if done else r.get("year"), year_source=source,
                       path=str(cur_front.relative_to(root)),
                       back_path=str(cur_back.relative_to(root)) if cur_back else "",
                       organized=datetime.now().isoformat(timespec="seconds"), meta=META if done else r.get("meta"),
                       description=r.get("description"))
        moved += cur_front != front
        changed += 1
    if changed:
        catalog.export_csv()
        for d in sorted(root.glob("*/*/"), reverse=True) + sorted(root.glob("*/")):  # tidy empty folders
            if d.name not in (INBOX, BACKS) and d.parent.name not in (INBOX, BACKS) and not any(d.iterdir()):
                d.rmdir()
    return moved


def remove_photo(catalog, pid):
    """Send a photo and its back scan to the Recycle Bin (so it can be restored) and forget it."""
    from win32com.shell import shell, shellcon
    row = catalog.rows("id = ?", (pid,))
    if not row:
        return
    for rel in (row[0].get("path"), row[0].get("back_path")):
        f = catalog.root / rel if rel else None
        if f and f.exists():
            shell.SHFileOperation((0, shellcon.FO_DELETE, str(f), None, shellcon.FOF_ALLOWUNDO |
                                   shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI,
                                   None, None))
            _remove_empty(f.parent, catalog.root)
    catalog.delete(pid)
    with catalog.lock:  # photos that pointed at it as their original no longer have one
        catalog.db.execute("UPDATE photos SET duplicate_of = NULL, meta = NULL WHERE duplicate_of = ?", (pid,))
        catalog.db.commit()
    catalog.export_csv()


def keep_both(catalog, a, b):
    """You said a and b are different photos: never pair them again."""
    for x, y in ((a, b), (b, a)):
        row = catalog.rows("id = ?", (x,))[0]
        distinct = set((row.get("distinct_from") or "").split()) | {y}
        dup = row.get("duplicate_of")
        catalog.upsert(x, distinct_from=" ".join(sorted(distinct)),
                       **({"duplicate_of": None, "meta": None} if dup == y else {}))
    organize(catalog)  # drops the Duplicate tag from the files


def _remove_empty(folder, root):
    """Delete folders a moved photo left empty (e.g. Unknown year/Wedding after its year was found)."""
    for d in (folder, folder.parent):
        if d != root and d.name not in (INBOX, BACKS) and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
