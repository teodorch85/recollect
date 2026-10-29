"""ReCollect: scan several prints at once on the V600, then their backs, and file them.

Workflow per bed:
  1. Place prints with ~1 cm gaps, close the lid, press Space  -> fronts are split, straightened, saved;
     an orange camera date printed in a corner ('94 7 15) is read too.
  2. Flip every print over in the same spot, press Space       -> backs are matched by position and
     read with Windows OCR; blank backs are discarded. (Press N instead to skip backs for this bed.)
  3. Photos are filed at once into <YEAR>/<Batch>/ by simple rules (see organizer.py), with the back
     text in the filename and everything recorded in photos.db / catalog.csv.
"""
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

import datestamp
import detect
import duplicates
import enhance
import ocr
import organizer
from version import __version__
from scanner import ScannerError, scan_bed

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"
REFERENCE_FILE = APP_DIR / "empty_bed.png"
PREVIEW_DPI = 150  # quick look at the bed: about 10 s, enough to find the prints
ICON_FILE = APP_DIR / "ReCollect.ico"
DEFAULTS = {
    "output": str(Path.home() / "Pictures" / "PhotoScans"),
    "front_dpi": 600,
    "back_dpi": 300,
    "format": "JPEG",
    "jpeg_quality": 100,
    "sharpen": "High",
    "restore_color": True,
    "scan_backs": True,
    "batch": "",
}


def load_settings():
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_image(path, bgr, dpi, fmt, quality):
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if fmt == "TIFF":
        img.save(path, dpi=(dpi, dpi), compression="tiff_lzw")
    else:
        img.save(path, quality=quality, subsampling=0, dpi=(dpi, dpi))


class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        self.events = queue.Queue()
        self.busy = False
        self.state = "front"          # "front" -> waiting for fronts, "back" -> waiting for backs
        self.bed = []                 # [{id, rect, dpi}] for prints on the glass right now
        self.reference = cv2.imread(str(REFERENCE_FILE)) if REFERENCE_FILE.exists() else None
        self._cat = None

        root.title(f"ReCollect {__version__}")
        root.iconbitmap(default=str(ICON_FILE))  # window, title bar and taskbar
        self.ui = root.winfo_fpixels("1i") / 96  # display scaling: 1.25 at 125%
        x, y, w, h = _work_area()
        width, height = min(int(1150 * self.ui), w - 20), h - 50  # fit above the taskbar
        root.geometry(f"{width}x{height}+{x + (w - width) // 2}+{y + 10}")
        root.minsize(int(760 * self.ui), int(720 * self.ui))  # room for the whole left column
        self._build_ui()
        self._save_settings()  # drops settings this version no longer uses (e.g. an old API key)
        root.bind("<space>", lambda e: self._space(e))
        root.bind("<n>", lambda e: self._key_skip(e))
        root.bind("<N>", lambda e: self._key_skip(e))
        root.bind("<p>", lambda e: self._key_preview(e))
        root.bind("<P>", lambda e: self._key_preview(e))
        root.after(100, self._poll)
        self._refresh()
        if self.reference is None:
            root.after(300, self._ask_calibrate)

    # ---------- UI ----------
    def _build_ui(self):
        # left: a column of settings and buttons; right: the bed preview, filling the rest
        side = ttk.Frame(self.root, padding=(8, 8, 4, 8), width=int(390 * self.ui))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)  # fixed width; long texts wrap instead of widening it

        files = ttk.LabelFrame(side, text="Photos", padding=6)
        files.pack(fill="x")
        ttk.Label(files, text="Save to:").grid(row=0, column=0, columnspan=2, sticky="w")
        self.out_var = tk.StringVar(value=self.s["output"])
        ttk.Entry(files, textvariable=self.out_var).grid(row=1, column=0, sticky="we")
        ttk.Button(files, text="Browse…", command=self._browse).grid(row=1, column=1, padx=(4, 0))
        ttk.Label(files, text="Batch / box label:").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.batch_var = tk.StringVar(value=self.s["batch"])
        ttk.Entry(files, textvariable=self.batch_var).grid(row=3, column=0, columnspan=2, sticky="we")
        ttk.Label(files, text="tip: put the year in it, e.g. “1994 Crete”",
                  foreground="#666").grid(row=4, column=0, columnspan=2, sticky="w")
        files.columnconfigure(0, weight=1)

        opts = ttk.LabelFrame(side, text="Scan settings", padding=6)
        opts.pack(fill="x", pady=(6, 0))
        self.dpi_var = tk.IntVar(value=self.s["front_dpi"])
        self.sharpen_var = tk.StringVar(value=self.s["sharpen"])
        self.fmt_var = tk.StringVar(value=self.s["format"])
        self.quality_var = tk.IntVar(value=self.s["jpeg_quality"])
        for row, (label, var, values) in enumerate((
                ("Resolution (dpi):", self.dpi_var, [300, 600, 800, 1200]),
                ("Unsharp mask:", self.sharpen_var, list(enhance.SHARPEN_LEVELS)),
                ("Format:", self.fmt_var, ["JPEG", "TIFF"]),
                ("JPEG quality:", self.quality_var, [90, 95, 100]))):
            ttk.Label(opts, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Combobox(opts, textvariable=var, values=values, width=8,
                         state="readonly").grid(row=row, column=1, sticky="w", padx=(6, 0), pady=2)
        self.restore_var = tk.BooleanVar(value=self.s["restore_color"])
        ttk.Checkbutton(opts, text="Color restoration", variable=self.restore_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.backs_var = tk.BooleanVar(value=self.s["scan_backs"])
        ttk.Checkbutton(opts, text="Scan backs", variable=self.backs_var,
                        command=self._backs_toggled).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(opts, text="Calibrate empty bed", command=self.calibrate).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        org = ttk.LabelFrame(side, text="Organize  (folders: Year / Batch)", padding=6)
        org.pack(fill="x", pady=(6, 0))
        ttk.Label(org, text="Set year for batch:").grid(row=0, column=0, columnspan=3, sticky="w")
        self.fix_batch_var = tk.StringVar()
        self.fix_batch = ttk.Combobox(org, textvariable=self.fix_batch_var, postcommand=self._fill_batches)
        self.fix_batch.grid(row=1, column=0, sticky="we")
        self.fix_year_var = tk.StringVar()
        ttk.Entry(org, textvariable=self.fix_year_var, width=6).grid(row=1, column=1, padx=4)
        ttk.Button(org, text="Apply", command=self._set_year).grid(row=1, column=2)
        links = ttk.Frame(org)
        links.grid(row=2, column=0, columnspan=3, sticky="we", pady=(6, 0))
        ttk.Button(links, text="Open photos folder", command=self._open_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(links, text="Open catalog (Excel)", command=self._open_catalog).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(org, text="Find duplicates…", command=self.find_duplicates).grid(row=3, column=0, columnspan=3, sticky="we", pady=(4, 0))
        org.columnconfigure(0, weight=1)

        # scanning controls at the bottom of the column, next to the preview they act on
        btns = ttk.Frame(side)
        btns.pack(side="bottom", fill="x")
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 14, "bold"), padding=10)
        self.preview_btn = ttk.Button(btns, text="Preview: check placement  [P]", command=self.preview)
        self.preview_btn.pack(fill="x", pady=(0, 6))
        self.main_btn = ttk.Button(btns, style="Big.TButton", command=self.main_action)
        self.main_btn.pack(fill="x")
        row = ttk.Frame(btns)
        row.pack(fill="x", pady=(6, 0))
        self.skip_btn = ttk.Button(row, text="No backs for this bed  [N]", command=self.skip_backs)
        self.skip_btn.pack(side="left", fill="x", expand=True)
        self.redo_btn = ttk.Button(row, text="Redo fronts", command=self.redo_fronts)
        self.redo_btn.pack(side="left", padx=(4, 0))
        self.dup_btn = ttk.Button(btns, text="Remove duplicates from this bed", command=self.remove_bed_duplicates)
        self.dup_btn.pack(fill="x", pady=(4, 0))
        self.count_var = tk.StringVar()
        ttk.Label(btns, textvariable=self.count_var, font=("Segoe UI", 11)).pack(anchor="w", pady=(6, 0))

        self.status = tk.StringVar()
        status = ttk.Label(side, textvariable=self.status, font=("Segoe UI", 12), padding=(0, 10))
        status.pack(side="bottom", fill="x")
        # rewrap the message to the column width instead of a fixed width
        status.bind("<Configure>", lambda e: status.config(wraplength=max(e.width - 4, 150)))

        # Fixed-size area (doesn't grow with the image); the preview is fitted inside it.
        self.view_area = tk.Frame(self.root, background="#333")
        self.view_area.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=8)
        self.view_area.pack_propagate(False)
        self.canvas = tk.Label(self.view_area, background="#333")
        self.canvas.place(relx=0.5, rely=0.5, anchor="center")
        self._view = None
        self._render_job = None
        self.view_area.bind("<Configure>", lambda e: self._schedule_render())

    def _schedule_render(self):
        """Refit the preview shortly after resizing stops (refitting on every pixel would lag)."""
        if self._render_job:
            self.root.after_cancel(self._render_job)
        self._render_job = self.root.after(60, self._render)

    def _typing(self, event):
        return isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox))

    def _space(self, event):
        if not self._typing(event):  # a space typed in the batch label must not start a scan
            self.main_action()

    def _key_preview(self, event):
        if not self._typing(event):
            self.preview()

    def _key_skip(self, event):
        if not self._typing(event):
            self.skip_backs()

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)
            self._refresh()

    def _backs_toggled(self):
        if not self.backs_var.get() and self.state == "back":
            self.skip_backs()
        self._refresh()

    def _refresh(self):
        has_dups = not self.busy and self.state == "back" and any(p.get("dup") for p in self.bed)
        self.dup_btn.config(state="normal" if has_dups else "disabled")
        self.preview_btn.config(state="normal" if not self.busy and self.state == "front" else "disabled")
        if self.busy:
            self.main_btn.config(state="disabled")
            self.skip_btn.config(state="disabled")
            self.redo_btn.config(state="disabled")
        elif self.state == "front":
            self.main_btn.config(text="Scan FRONTS  [Space]", state="normal")
            self.skip_btn.config(state="disabled")
            self.redo_btn.config(state="disabled")
            self.status.set("Place prints face-down with ~1 cm gaps, close the lid, press Space.")
        else:
            self.main_btn.config(text="Scan BACKS  [Space]", state="normal")
            self.skip_btn.config(state="normal")
            self.redo_btn.config(state="normal")
            self.status.set(f"Saved {len(self.bed)} fronts. Flip each print over IN THE SAME SPOT, "
                            "close the lid, press Space.  (N = no backs for this bed)")
        try:
            self.count_var.set(f"Photos: {self._catalog().count()}")
        except OSError:
            self.count_var.set("")

    def _show(self, bgr):
        self._view = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        self._render()

    def _render(self):
        """Fit the last bed preview inside the preview area (called again on window resize)."""
        if self._view is None:
            return
        w, h = self.view_area.winfo_width() - 8, self.view_area.winfo_height() - 8
        if w < 50 or h < 50:
            return
        img = self._view.copy()
        f = min(w / img.width, h / img.height)
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.config(image=self._photo)

    # ---------- files & catalogue ----------
    def _root(self):
        p = Path(self.out_var.get())
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _out(self):
        """New scans wait in the inbox until their back step is done, then get filed."""
        p = self._root() / organizer.INBOX
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _catalog(self):
        root = self._root()
        if self._cat is None or self._cat.root != root:
            self._cat = organizer.Catalog(root)
        return self._cat

    def _ext(self):
        return ".tif" if self.fmt_var.get() == "TIFF" else ".jpg"

    def _next_id(self):
        ids = [int(f.name[:5]) for f in self._out().iterdir() if re.match(r"\d{5}[._]", f.name)]
        return max(max(ids, default=0), self._catalog().max_id()) + 1

    def _rel(self, path):
        return str(path.relative_to(self._root()))

    def _file_bed(self):
        """Mark the bed's photos ready and run the filing rules; returns a short summary."""
        cat = self._catalog()
        ids = [p["id"] for p in self.bed]
        for pid in ids:
            cat.upsert(pid, status="ready")
        organizer.organize(cat)
        rows = [r for r in cat.rows() if r["id"] in ids]
        places = sorted({str(Path(r["path"]).parent) for r in rows})
        return "Filed into " + ", ".join(places) if places else ""

    def _fill_batches(self):
        self.fix_batch["values"] = organizer.batches(self._catalog())

    def _set_year(self):
        batch, year = self.fix_batch_var.get().strip(), self.fix_year_var.get().strip()
        if not batch or (year and not re.fullmatch(r"(19|20)\d\d", year)):
            messagebox.showinfo("Set year", "Pick a batch and type a year like 1994 (empty = automatic).")
            return
        moved = organizer.set_batch_year(self._catalog(), batch, year)
        self.status.set(f"Batch “{batch}” → {year or 'automatic year'}: {moved} photo(s) moved.")
        self._refresh()

    def _open_folder(self):
        os.startfile(self._root())

    def _open_catalog(self):
        cat = self._catalog()
        cat.export_csv()
        os.startfile(cat.root / "catalog.csv")

    def _save_settings(self):
        self.s.update(output=self.out_var.get(), front_dpi=int(self.dpi_var.get()), format=self.fmt_var.get(),
                      jpeg_quality=int(self.quality_var.get()), sharpen=self.sharpen_var.get(),
                      restore_color=bool(self.restore_var.get()),
                      scan_backs=bool(self.backs_var.get()), batch=self.batch_var.get())
        try:
            SETTINGS_FILE.write_text(json.dumps(self.s, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---------- actions ----------
    def _ask_calibrate(self):
        if messagebox.askokcancel("Calibrate", "First, a one-time calibration.\n\nRemove everything from "
                                  "the glass, close the lid, then press OK.\n(Takes about 15 seconds.)"):
            self.calibrate()

    def calibrate(self):
        if self.busy:
            return
        self._run(self._do_calibrate, "Scanning the empty bed for calibration…")

    def main_action(self):
        if self.busy:
            return
        self._save_settings()
        if self.state == "front":
            self._run(self._do_fronts, f"Scanning fronts at {self.dpi_var.get()} dpi…")
        else:
            self._run(self._do_backs, "Scanning backs…")

    def preview(self):
        if self.busy or self.state != "front":
            return
        self._run(self._do_preview, "Preview scan…")

    def remove_bed_duplicates(self):
        """Throw away the fronts just scanned that repeat photos already in the library."""
        if self.busy or self.state != "back":
            return
        dups = [p for p in self.bed if p.get("dup")]
        for p in dups:
            organizer.remove_photo(self._catalog(), p["id"])
        self.bed = [p for p in self.bed if not p.get("dup")]
        if not self.bed:
            self.state = "front"
        self._refresh()
        self.status.set(f"Removed {len(dups)} duplicate(s) (in the Recycle Bin). "
                        + ("Flip the other prints IN THE SAME SPOT and press Space." if self.bed else
                           "Place the next prints and press Space."))

    def find_duplicates(self):
        if not self.busy:
            self._run(self._do_find_duplicates, "Looking for duplicates…")

    def skip_backs(self):
        if self.busy or self.state != "back":
            return
        for p in self.bed:
            self._catalog().upsert(p["id"], back_status="not scanned")
        summary = self._file_bed()
        self.state, self.bed = "front", []
        self._refresh()
        self.status.set(summary + ". Place the next prints and press Space.")

    def redo_fronts(self):
        """Delete the fronts just saved (e.g. two prints merged) so the bed can be rescanned."""
        if self.busy or self.state != "back":
            return
        out = self._out()
        for p in self.bed:
            for f in out.glob(f"{p['id']}*"):
                f.unlink()
            self._catalog().delete(p["id"])
        self.state, self.bed = "front", []
        self._refresh()
        self.status.set("Last fronts deleted. Fix the placement, then press Space to scan again.")

    def _run(self, fn, message):
        self.busy = True
        self._refresh()
        self.status.set(message)
        threading.Thread(target=self._worker, args=(fn,), daemon=True).start()

    def _worker(self, fn):
        try:
            self.events.put(("done", fn()))
        except ScannerError as e:
            self.events.put(("error", str(e)))
        except Exception as e:  # keep the app alive whatever happens
            self.events.put(("error", f"{type(e).__name__}: {e}"))

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "note":  # progress message from a job that is still running
                    self.status.set(payload)
                    continue
                self.busy = False
                if kind == "error":
                    self._refresh()
                    self.status.set("⚠ " + payload)
                else:
                    view, message, *then = payload  # then: optional follow-up on the UI thread
                    self._refresh()
                    if view is not None:
                        self._show(view)
                    if message:
                        self.status.set(message)
                    for fn in then:
                        fn()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ---------- work (runs in background thread) ----------
    def _do_calibrate(self):
        img = scan_bed(detect.WORK_DPI)
        cv2.imwrite(str(REFERENCE_FILE), img)
        self.reference = img
        return img, "Calibrated. Place prints face-down with ~1 cm gaps, close the lid, press Space."

    def _do_fronts(self):
        dpi = int(self.dpi_var.get())
        t = time.time()
        img = scan_bed(dpi)
        backs = detect.find_backs(img, dpi, self.reference)
        if backs:  # saving these would file blank paper as photos
            return (detect.draw_overlay(img, backs, alert=range(len(backs))),
                    f"Nothing saved: {len(backs)} print{'s are' if len(backs) > 1 else ' is'} lying back-side "
                    "down (red). Turn them face-down and press Space again.")
        rects = detect.find_photos(img, dpi, self.reference)
        if not rects:
            return detect.draw_overlay(img, []), "No prints found on the glass. Check placement and try again."
        out, ext, first, cat = self._out(), self._ext(), self._next_id(), self._catalog()
        scanned = datetime.now().isoformat(timespec="seconds")
        self.bed, stamps = [], 0
        known = [(r["id"], r["fingerprint"]) for r in cat.rows("fingerprint IS NOT NULL AND fingerprint != ''")]
        for i, r in enumerate(rects):
            pid = f"{first + i:05d}"
            photo = enhance.process_front(detect.crop(img, r, dpi), dpi, self.s["sharpen"], self.s["restore_color"])
            path = out / f"{pid}{ext}"
            save_image(path, photo, dpi, self.s["format"], self.s["jpeg_quality"])
            size_cm, fmt = organizer.print_size(photo.shape[1], photo.shape[0], dpi)
            try:
                stamp = datestamp.read(photo, dpi)
            except Exception:  # only a hint for the year: never lose a scan over it
                stamp = ""
            stamps += bool(stamp)
            prints, dup = self._duplicate_check(photo, known)
            cat.upsert(pid, status="scanning", batch=self.batch_var.get().strip(), scanned=scanned,
                       bed=f"{first:05d}", dpi=dpi, size_cm=size_cm, print_format=fmt, date_stamp=stamp,
                       back_status="pending", path=self._rel(path), back_path="",
                       fingerprint=prints, duplicate_of=dup)
            known.append((pid, prints))
            self.bed.append({"id": pid, "rect": r, "dpi": dpi, "dup": dup})
        labels = [p["id"] + (f" = {p['dup']}" if p["dup"] else "") for p in self.bed]
        dups = [i for i, p in enumerate(self.bed) if p["dup"]]
        view = detect.draw_overlay(img, rects, labels, alert=dups)
        took = f"{time.time() - t:.0f}s" + (f", {stamps} with a camera date" if stamps else "")
        warn = ""
        if dups:
            warn = (f"⚠ {len(dups)} scanned before (red, “= original”): "
                    + ("press “Remove duplicates” to drop them. " if self.backs_var.get() else
                       "kept and tagged Duplicate; review them with Find duplicates. "))
        if self.backs_var.get():
            self.state = "back"
            return view, (f"Saved {len(rects)} fronts ({took}). {warn}Check the outlines: one box per print? "
                          "Then flip each print IN THE SAME SPOT and press Space.  (N = skip backs, "
                          "Redo fronts = boxes wrong)")
        for p in self.bed:
            cat.upsert(p["id"], back_status="not scanned")
        summary = self._file_bed()
        self.bed = []
        return view, f"Saved {len(rects)} photos ({took}). {warn}{summary}. Place the next prints and press Space."

    def _duplicate_check(self, photo, known):
        """(fingerprints of this front, id of the library photo it repeats or None)."""
        try:
            prints = duplicates.fingerprints(photo)
            match = duplicates.best_match(photo, prints, known, self._load_photo)
        except Exception:  # a hint only: never lose a scan over it
            return None, None
        if not match:
            return prints, None
        row = self._catalog().rows("id = ?", (match[0],))
        return prints, (row[0].get("duplicate_of") or match[0]) if row else match[0]

    def _load_photo(self, pid):
        row = self._catalog().rows("id = ?", (pid,))
        return duplicates.read_image(self._catalog().root / row[0]["path"]) if row and row[0].get("path") else None

    def _prints_on_glass(self, img, dpi):
        """(rects of every print, indices of the ones lying back-side down)."""
        backs = detect.find_backs(img, dpi, self.reference)
        # the picture detector boxes watermark text on white backs: let the paper outlines replace it
        photos = [r for r in detect.find_photos(img, dpi, self.reference, refine=False)
                  if not any(detect.inside(r[0], b) for b in backs)]
        rects = detect.reading_order(photos + backs, dpi)
        return rects, {i for i, r in enumerate(rects) if r in backs}

    def _do_preview(self):
        img = scan_bed(PREVIEW_DPI)
        rects, backs = self._prints_on_glass(img, PREVIEW_DPI)
        if not rects:
            return detect.draw_overlay(img, []), "Preview: no prints found on the glass."
        problems, bad = detect.check_placement(rects, img.shape, PREVIEW_DPI)
        if backs:
            problems.insert(0, f"{detect.print_names(sorted(backs))} {'is' if len(backs) == 1 else 'are'} "
                               "back-side down: turn face-down")
        view = detect.draw_overlay(img, rects, alert=bad | backs)
        found = f"Preview: {len(rects)} print{'s' if len(rects) > 1 else ''} found."
        if problems:
            text = "; ".join(problems)
            return view, f"{found} ⚠ {text[0].upper()}{text[1:]}. Fix and press P again."
        return view, f"{found} Placement looks good. If that's the right number, press Space to scan."

    def _do_find_duplicates(self):
        cat = self._catalog()
        rows = cat.rows("status IN ('ready', 'filed')")
        for i, r in enumerate(rows):  # photos scanned before this feature have no fingerprint yet
            if not r.get("fingerprint"):
                if i % 10 == 0:
                    self.events.put(("note", f"Fingerprinting older photos… {i}/{len(rows)}"))
                img = duplicates.read_image(cat.root / r["path"])
                if img is not None:
                    r["fingerprint"] = duplicates.fingerprints(img)
                    cat.upsert(r["id"], fingerprint=r["fingerprint"])
        pairs = duplicates.find_all(rows, self._load_photo, lambda i, n: self.events.put(
            ("note", f"Comparing photos… {i}/{n}")))
        changed = False
        for dup, orig, _ in pairs:
            row = next(r for r in rows if r["id"] == dup)
            if row.get("duplicate_of") != orig:
                cat.upsert(dup, duplicate_of=orig, meta=None)  # tag it Duplicate in the file too
                changed = True
        if changed:
            organizer.organize(cat)
        if not pairs:
            return None, f"No duplicates among {len(rows)} photos."
        return (None, f"Found {len(pairs)} duplicate(s) among {len(rows)} photos.",
                lambda: DuplicateReview(self, [(d, o) for d, o, _ in pairs]))

    def _do_backs(self):
        # Backs are only read for text, so scan just the strip the prints occupy:
        # head travel, not resolution, is what costs time on the V600.
        dpi = int(self.s["back_dpi"])
        front_rects = [self._scale_rect(p["rect"], p["dpi"], dpi) for p in self.bed]
        pts = np.vstack([cv2.boxPoints(r) for r in front_rects]) / dpi
        margin = 0.4  # inches, room for prints that shifted a little when flipped
        x0, y0 = pts.min(axis=0) - margin
        x1, y1 = pts.max(axis=0) + margin
        region = (max(x0, 0), max(y0, 0), x1 - max(x0, 0), y1 - max(y0, 0))
        img = scan_bed(dpi, region=region, background=self.reference)
        found = detect.find_photos(img, dpi, self.reference, refine=False)
        backs = detect.pair_backs(front_rects, found, dpi)
        out, ext, cat = self._out(), self._ext(), self._catalog()
        labels, kept = [], 0
        for p, r in zip(self.bed, backs):
            back = detect.crop(img, r, dpi)
            if detect.is_blank(back, dpi):
                cat.upsert(p["id"], back_status="blank")
                labels.append(f"{p['id']} blank")
                continue
            path = out / f"{p['id']}_back{ext}"
            save_image(path, back, dpi, self.s["format"], self.s["jpeg_quality"])
            self.events.put(("note", f"Reading back of {p['id']}…"))
            personal, lab, paper = organizer.split_back_text(ocr.read_text(back))
            cat.upsert(p["id"], back_status="saved", back_path=self._rel(path),
                       back_text=personal, back_lab=lab, paper=paper)
            labels.append(f"{p['id']} " + ("text" if personal else "back"))
            kept += 1
        n = len(self.bed)
        summary = self._file_bed()
        self.state, self.bed = "front", []
        return (detect.draw_overlay(img, backs, labels),
                f"Backs: {kept} kept, {n - kept} blank. {summary}. Place the next prints and press Space.")

    @staticmethod
    def _scale_rect(rect, from_dpi, to_dpi):
        f = to_dpi / from_dpi
        (cx, cy), (w, h), a = rect
        return (cx * f, cy * f), (w * f, h * f), a


class DuplicateReview:
    """Window that shows each duplicate next to its original; you choose which to keep."""

    def __init__(self, app, pairs):
        self.app, self.pairs, self.i, self.gone = app, pairs, -1, set()
        self.win = tk.Toplevel(app.root)
        self.win.title("Duplicates")
        self.win.transient(app.root)
        ui = app.ui
        self.side = int(420 * ui)  # each picture fits in this square
        self.win.geometry(f"{int(920 * ui)}x{int(640 * ui)}")
        self.title = tk.StringVar()
        ttk.Label(self.win, textvariable=self.title, font=("Segoe UI", 12), padding=8).pack(anchor="w")
        row = ttk.Frame(self.win, padding=(8, 0))
        row.pack(fill="both", expand=True)
        self.cols = []
        for side in ("left", "right"):
            col = ttk.Frame(row)
            col.pack(side="left", fill="both", expand=True, padx=4)
            img = tk.Label(col, background="#333")
            img.pack(fill="both", expand=True)
            info = tk.StringVar()
            ttk.Label(col, textvariable=info, wraplength=self.side, padding=(0, 4)).pack(anchor="w")
            ttk.Button(col, text="Delete this one", command=lambda s=side: self._delete(s)).pack(fill="x")
            self.cols.append((img, info))
        bar = ttk.Frame(self.win, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Different photos: keep both", command=self._keep_both).pack(side="left")
        ttk.Button(bar, text="Decide later", command=self._next).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=self.win.destroy).pack(side="right")
        self._next()

    def _next(self):
        self.i += 1
        while self.i < len(self.pairs) and self.gone & set(self.pairs[self.i]):
            self.i += 1  # one of the two was deleted meanwhile
        if self.i >= len(self.pairs):
            self.win.destroy()
            self.app._refresh()
            self.app.status.set("Duplicates reviewed. Deleted photos are in the Recycle Bin.")
            return
        cat = self.app._catalog()
        original, dup = self.pairs[self.i][1], self.pairs[self.i][0]
        self.title.set(f"{self.i + 1} of {len(self.pairs)}: the right one looks like a second scan of the left one.")
        self.photos = []
        for (img, info), pid in zip(self.cols, (original, dup)):
            row = cat.rows("id = ?", (pid,))[0]
            bgr = duplicates.read_image(cat.root / row["path"])
            if bgr is not None:
                pic = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                pic.thumbnail((self.side, self.side))
                self.photos.append(ImageTk.PhotoImage(pic))
                img.config(image=self.photos[-1])
            facts = [pid, row.get("size_cm") and f"{row['size_cm']} cm", row.get("dpi") and f"{row['dpi']} dpi",
                     row.get("year") or "undated", row.get("batch")]
            info.set("  ·  ".join(f for f in facts if f) + f"\n{row['path']}")
        self.ids = (original, dup)

    def _delete(self, side):
        pid = self.ids[0 if side == "left" else 1]
        organizer.remove_photo(self.app._catalog(), pid)
        self.gone.add(pid)
        self._next()

    def _keep_both(self):
        organizer.keep_both(self.app._catalog(), *self.ids)
        self._next()


def _own_taskbar_entry():
    """Group the window under ReCollect's own icon in the taskbar, not under Python's."""
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ReCollect.PhotoScans")


def _dpi_aware():
    """Draw at the screen's real resolution. Without this, Windows stretches the window as a bitmap
    on scaled displays (125%, 150%...), which blurs the text and makes the window too big."""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def _work_area():
    """Usable screen area (without the taskbar) in pixels: (left, top, width, height)."""
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


if __name__ == "__main__":
    _own_taskbar_entry()
    _dpi_aware()
    root = tk.Tk()
    App(root)
    root.mainloop()
