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
import subprocess
import sys
import tempfile
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
import notify
import ocr
import organizer
import namer
import naming
import rotate
import settings_window
import updater
from version import __version__
from scanner import ScannerError, reset_scanner, scan_bed

APP_DIR = Path(__file__).resolve().parent  # the app's own files (inside ReCollect.exe: its _internal folder)
# Settings and calibration: next to the code when run from source; for ReCollect.exe in the user's
# AppData, so an update can replace the program folder without losing them.
DATA_DIR = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReCollect") if updater.FROZEN else APP_DIR
SETTINGS_FILE = DATA_DIR / "settings.json"
REFERENCE_FILE = DATA_DIR / "empty_bed.png"
# title of the Windows notification when each job finishes
JOB_TITLES = {"_do_fronts": "Fronts scanned", "_do_backs": "Backs read, photos filed", "_do_preview": "Preview ready",
              "_do_find_duplicates": "Duplicate check finished", "_do_calibrate": "Scanner calibrated",
              "_do_reset_scanner": "Scanner reset", "_do_update": "Update downloaded",
              "_do_turn_batch": "Photos turned upright", "_do_refile": "Library refiled"}
SCANNER_JOBS = ("_do_fronts", "_do_backs", "_do_preview", "_do_calibrate")
UPDATE_CHECK_MS = 60_000  # how often to look for a newer version in this folder (from source)
ONLINE_CHECK_S = 6 * 3600  # how often ReCollect.exe asks GitHub for a new release
MONTHS = ["—", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PREVIEW_DPI = 150  # quick look at the bed: about 10 s, enough to find the prints


def scan_seconds(dpi, share=1.0):
    """Rough V600 time for a pass over `share` of the bed (it reports no progress of its own):
    about 15 s up to 300 dpi, 65 s at 600 dpi."""
    return max(8.0, (15 + max(0, dpi - 300) / 300 * 50) * share)


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
    "notify": True,         # popup when a job finishes
    "flash": True,          # flash the taskbar button when the window isn't in front
    "check_updates": True,
    "auto_rotate": False,   # ask Claude which way is up (needs an API key)
    "name_template": naming.DEFAULT,  # folders and file names, Immich storage-template style
    "shortcuts_for": "",    # the ReCollect.exe the Desktop and Start menu shortcuts point to
}


def disk_version():
    """The version now in this folder (a release replaces the files while the app keeps running)."""
    try:
        m = re.search(r'__version__\s*=\s*"([\d.]+)"', (APP_DIR / "version.py").read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _newer(a, b):
    try:
        return tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))
    except ValueError:  # version.py caught half-written during an update
        return False


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


class Cancelled(Exception):
    """The Cancel button was pressed; raised at the job's next safe stopping point."""


class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        self.events = queue.Queue()
        self.busy = False
        self.cancelling = threading.Event()
        self.state = "front"          # "front" -> waiting for fronts, "back" -> waiting for backs
        self.bed = []                 # [{id, rect, dpi}] for prints on the glass right now
        self.reference = cv2.imread(str(REFERENCE_FILE)) if REFERENCE_FILE.exists() else None
        self._cat = None
        self.restart_requested = False
        self._offered = None  # version already offered, so it is announced once
        self._release = None  # (version, zip url, page) of a newer release on GitHub
        self._last_online = time.time() - ONLINE_CHECK_S + 60  # first look a minute after start
        self.app_dir = APP_DIR
        self._settings_win = None

        root.title(f"ReCollect {__version__}")
        root.iconbitmap(default=str(ICON_FILE))  # window, title bar and taskbar
        self.ui = root.winfo_fpixels("1i") / 96  # display scaling: 1.25 at 125%
        x, y, w, h = _work_area()
        width, height = min(int(1150 * self.ui), w - 20), h - 50  # fit above the taskbar
        root.geometry(f"{width}x{height}+{x + (w - width) // 2}+{y + 10}")
        root.minsize(int(760 * self.ui), int(720 * self.ui))  # room for the whole left column
        try:
            organizer.template = naming.parse(self.s["name_template"])
        except naming.TemplateError:  # edited by hand into something invalid: keep the default
            self.s["name_template"] = naming.DEFAULT
        self._make_vars()
        self._build_ui()
        self._save_settings()  # drops settings this version no longer uses (e.g. an old API key)
        root.bind("<space>", lambda e: self._space(e))
        root.bind("<n>", lambda e: self._key_skip(e))
        root.bind("<N>", lambda e: self._key_skip(e))
        root.bind("<p>", lambda e: self._key_preview(e))
        root.bind("<P>", lambda e: self._key_preview(e))
        root.bind("<Escape>", lambda e: self.cancel())
        root.bind("<Control-comma>", lambda e: self.open_settings())
        root.after(100, self._poll)
        root.after(UPDATE_CHECK_MS, self._check_update)
        self._refresh()
        if self.reference is None:
            root.after(300, self._ask_calibrate)

    # ---------- UI ----------
    def _make_vars(self):
        """Settings as Tk variables, shared by the main window and the Settings window; saved on every change."""
        s = self.s
        self.out_var = tk.StringVar(value=s["output"])
        self.batch_var = tk.StringVar(value=s["batch"])
        self.dpi_var = tk.IntVar(value=s["front_dpi"])
        self.back_dpi_var = tk.IntVar(value=s["back_dpi"])
        self.sharpen_var = tk.StringVar(value=s["sharpen"])
        self.fmt_var = tk.StringVar(value=s["format"])
        self.quality_var = tk.IntVar(value=s["jpeg_quality"])
        self.restore_var = tk.BooleanVar(value=s["restore_color"])
        self.backs_var = tk.BooleanVar(value=s["scan_backs"])
        self.notify_var = tk.BooleanVar(value=s["notify"])
        self.flash_var = tk.BooleanVar(value=s["flash"])
        self.updates_var = tk.BooleanVar(value=s["check_updates"])
        self.rotate_var = tk.BooleanVar(value=s["auto_rotate"])
        for var in (self.out_var, self.dpi_var, self.back_dpi_var, self.sharpen_var, self.fmt_var,
                    self.quality_var, self.restore_var, self.backs_var, self.notify_var, self.flash_var,
                    self.updates_var, self.rotate_var):
            var.trace_add("write", lambda *a: self._settings_changed())

    def _settings_changed(self):
        if not hasattr(self, "summary_var"):
            return  # still building the window
        self._save_settings()
        fmt = "TIFF" if self.fmt_var.get() == "TIFF" else f"JPEG {self.quality_var.get()}"
        backs = f", backs {self.back_dpi_var.get()} dpi" if self.backs_var.get() else ", no backs"
        turn = ", auto-rotate" if self.rotate_var.get() else ""
        self.summary_var.set(f"Fronts {self.dpi_var.get()} dpi{backs}, {fmt}{turn}.\nSaving to {self.out_var.get()}")

    def open_settings(self):
        if self._settings_win and self._settings_win.win.winfo_exists():
            self._settings_win.win.lift()
            self._settings_win.win.focus_force()
        else:
            self._settings_win = settings_window.SettingsWindow(self)

    def restore_defaults(self):
        """Back to DEFAULTS, except where photos go and the batch being scanned."""
        for var, key in ((self.dpi_var, "front_dpi"), (self.back_dpi_var, "back_dpi"), (self.sharpen_var, "sharpen"),
                         (self.fmt_var, "format"), (self.quality_var, "jpeg_quality"),
                         (self.restore_var, "restore_color"), (self.backs_var, "scan_backs"),
                         (self.notify_var, "notify"), (self.flash_var, "flash"), (self.updates_var, "check_updates"),
                         (self.rotate_var, "auto_rotate")):
            var.set(DEFAULTS[key])
        self._backs_toggled()

    def _build_ui(self):
        # left: a column of settings and buttons; right: the bed preview, filling the rest
        side = ttk.Frame(self.root, padding=(8, 8, 4, 8), width=int(390 * self.ui))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)  # fixed width; long texts wrap instead of widening it

        self.update_box = tk.Frame(side, background="#E1F5EE", padx=8, pady=6)  # packed when an update arrives
        self.update_text = tk.StringVar()
        tk.Label(self.update_box, textvariable=self.update_text, background="#E1F5EE", foreground="#085041",
                 font=("Segoe UI", 10, "bold"), wraplength=int(340 * self.ui), justify="left").pack(anchor="w")
        row = tk.Frame(self.update_box, background="#E1F5EE")
        row.pack(anchor="w", pady=(4, 0))
        self.update_btn = ttk.Button(row, text="Restart now", command=self.restart)
        self.update_btn.pack(side="left")
        ttk.Button(row, text="What's new", command=lambda: self._whats_new(self._release and self._release[2])
                   ).pack(side="left", padx=4)
        ttk.Button(row, text="Later", command=self.update_box.pack_forget).pack(side="left")

        files = ttk.LabelFrame(side, text="This batch", padding=6)
        files.pack(fill="x")
        self._first_box = files
        ttk.Label(files, text="Batch / box label:").grid(row=0, column=0, sticky="w")
        ttk.Entry(files, textvariable=self.batch_var).grid(row=1, column=0, sticky="we")
        ttk.Label(files, text="tip: put the year in it, e.g. “1994 Crete”",
                  foreground="#666").grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(files, text="Scan backs", variable=self.backs_var,
                        command=self._backs_toggled).grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.summary_var = tk.StringVar()
        ttk.Label(files, textvariable=self.summary_var, foreground="#666", wraplength=int(350 * self.ui),
                  justify="left").grid(row=4, column=0, sticky="w", pady=(4, 0))
        row = ttk.Frame(files)
        row.grid(row=5, column=0, sticky="we", pady=(6, 0))
        ttk.Button(row, text="⚙  Settings…  [Ctrl+,]", command=self.open_settings).pack(side="left", fill="x",
                                                                                     expand=True)
        ttk.Button(row, text="Check for updates", command=self.check_updates_clicked).pack(side="left", padx=(4, 0))
        files.columnconfigure(0, weight=1)
        self._settings_changed()

        org = ttk.LabelFrame(side, text="Organize  (folders: Year / Batch)", padding=6)
        org.pack(fill="x", pady=(6, 0))
        ttk.Label(org, text="Set year and month for batch:").grid(row=0, column=0, columnspan=4, sticky="w")
        self.fix_batch_var = tk.StringVar()
        self.fix_batch = ttk.Combobox(org, textvariable=self.fix_batch_var, postcommand=self._fill_batches)
        self.fix_batch.grid(row=1, column=0, sticky="we")
        self.fix_batch.bind("<<ComboboxSelected>>", lambda e: self._show_batch_date())
        self.fix_year_var = tk.StringVar()
        ttk.Entry(org, textvariable=self.fix_year_var, width=6).grid(row=1, column=1, padx=(4, 0))
        self.fix_month_var = tk.StringVar(value=MONTHS[0])
        ttk.Combobox(org, textvariable=self.fix_month_var, values=MONTHS, width=5,
                     state="readonly").grid(row=1, column=2, padx=4)
        ttk.Button(org, text="Apply", command=self._set_year).grid(row=1, column=3)
        batch_row = ttk.Frame(org)
        batch_row.grid(row=2, column=0, columnspan=4, sticky="we", pady=(4, 0))
        ttk.Button(batch_row, text="Rename batch…", command=self.rename_batch).pack(side="left", fill="x", expand=True)
        ttk.Button(batch_row, text="Turn upright…", command=self.turn_batch_upright).pack(side="left", fill="x",
                                                                                        expand=True, padx=(4, 0))
        links = ttk.Frame(org)
        links.grid(row=3, column=0, columnspan=4, sticky="we", pady=(6, 0))
        ttk.Button(links, text="Open photos folder", command=self._open_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(links, text="Open catalog (Excel)", command=self._open_catalog).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(org, text="Find duplicates…", command=self.find_duplicates).grid(row=4, column=0, columnspan=4, sticky="we", pady=(4, 0))
        org.columnconfigure(0, weight=1)

        # scanning controls at the bottom of the column, next to the preview they act on
        btns = ttk.Frame(side)
        btns.pack(side="bottom", fill="x")
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 14, "bold"), padding=10)
        top_row = ttk.Frame(btns)
        top_row.pack(fill="x", pady=(0, 6))
        self.preview_btn = ttk.Button(top_row, text="Preview: check placement  [P]", command=self.preview)
        self.preview_btn.pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(top_row, text="Cancel  [Esc]", command=self.cancel)
        self.cancel_btn.pack(side="left", padx=(4, 0))
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

        self.bar = ttk.Progressbar(side, maximum=1.0)
        self.bar.pack(side="bottom", fill="x", pady=(6, 0))
        self._phase = None  # (from, to, start time, expected seconds) while a scan is under way

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
        self.canvas.bind("<Button-1>", lambda e: self._click_view(e, -90))  # arrow turns clockwise
        self.canvas.bind("<Button-3>", lambda e: self._click_view(e, 90))
        self._clickable = None  # the fronts on show, which a click turns
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
        if self.busy or self.state == "back":  # the prints on the glass belong to the current folder
            messagebox.showinfo("Photos folder", "Finish the prints on the glass first (scan or skip their backs), "
                                                 "then change the folder.", parent=self._settings_parent())
            return
        d = filedialog.askdirectory(initialdir=self.out_var.get(), parent=self._settings_parent())
        if d:
            self.out_var.set(d)
            self._refresh()

    def _settings_parent(self):
        win = self._settings_win and self._settings_win.win
        return win if win and win.winfo_exists() else self.root

    def _backs_toggled(self):
        if not self.backs_var.get() and self.state == "back":
            self.skip_backs()
        self._refresh()

    def _refresh(self):
        has_dups = not self.busy and self.state == "back" and any(p.get("dup") for p in self.bed)
        self.dup_btn.config(state="normal" if has_dups else "disabled")
        self.preview_btn.config(state="normal" if not self.busy and self.state == "front" else "disabled")
        self.cancel_btn.config(state="normal" if self.busy and not self.cancelling.is_set() else "disabled")
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
        self._clickable = None  # a fronts view makes itself clickable again right after
        self._view = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        self._render()

    # ---------- turning photos in the preview (EXIF orientation only) ----------
    def _set_clickable(self, img, rects, labels, alert, pids, auto):
        self._clickable = {"img": img, "rects": rects, "labels": labels, "alert": alert, "pids": pids,
                           "auto": auto, "turn": [0] * len(pids)}
        self._draw_clickable()

    def _draw_clickable(self):
        c = self._clickable
        ups = [detect.top_direction(r, a + t) for r, a, t in zip(c["rects"], c["auto"], c["turn"])]
        labels = [lab + (f" +{t}" if t else "") for lab, t in zip(c["labels"], c["turn"])]
        self._view = Image.fromarray(cv2.cvtColor(detect.draw_overlay(c["img"], c["rects"], labels, alert=c["alert"],
                                                                      ups=ups), cv2.COLOR_BGR2RGB))
        self._render()

    def _click_view(self, event, delta):
        """Turn the clicked photo a quarter turn: only its EXIF orientation changes, not its pixels."""
        c = self._clickable
        if not c or self.busy or not getattr(self, "_photo", None):
            return
        shown_w, shown_h = self._photo.width(), self._photo.height()
        x = event.x - (self.canvas.winfo_width() - shown_w) / 2
        y = event.y - (self.canvas.winfo_height() - shown_h) / 2
        to_bed = c["img"].shape[1] / shown_w  # preview pixel -> scan pixel (same aspect ratio)
        i = detect.hit(c["rects"], x * to_bed, y * to_bed)
        if i is None:
            return
        pid, cat = c["pids"][i], self._catalog()
        row = cat.rows("id = ?", (pid,))
        if not row:  # deleted meanwhile (Redo fronts, Remove duplicates)
            return
        c["turn"][i] = (c["turn"][i] + delta) % 360
        cat.upsert(pid, turn=c["turn"][i], meta="")  # meta="": organize() writes the orientation into the file
        if row[0]["status"] in ("ready", "filed"):
            organizer.organize(cat)  # photos still waiting for backs get it when they are filed
        self._draw_clickable()
        then = " Then flip the prints and press Space." if self.state == "back" else ""
        self.status.set(f"{pid} turned {c['turn'][i]}° (the arrow shows its top). Click again to keep turning, "
                        f"right-click to turn the other way. Only the orientation tag changes.{then}")

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
            organizer.finish_unfinished(self._cat, keep={p["id"] for p in self.bed})
            organizer.organize(self._cat)  # files stuck photos, applies new naming rules; quick when nothing changed
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
        summary = "Filed into " + ", ".join(places) if places else ""
        return " ".join(t for t in (summary, organizer.problem_text()) if t)

    def _fill_batches(self):
        self.fix_batch["values"] = organizer.batches(self._catalog())

    def rename_batch(self):
        batch = self._picked_batch("Rename batch")
        if batch:
            RenameBatch(self, batch)

    def apply_template(self, template):
        """Use a new naming template and refile the whole library to match."""
        organizer.template = naming.parse(template)
        self.s["name_template"] = template
        self._save_settings()
        self._run(self._do_refile, "Renaming and moving photos to the new template…")

    def _do_refile(self):
        cat = self._catalog()
        moved = organizer.organize(cat)
        return None, " ".join(t for t in (f"Naming template applied: {moved} photo(s) moved or renamed.",
                                          organizer.problem_text()) if t)

    def _picked_batch(self, what):
        """The batch chosen in Organize, or None (after saying why)."""
        batch = self.fix_batch_var.get().strip()
        if not any(organizer.same_batch(b, batch) for b in organizer.batches(self._catalog())):
            messagebox.showinfo(what, "First pick the batch in the list above.")
            return None
        if self.busy:
            messagebox.showinfo(what, "Wait for the current job to finish first.")
            return None
        return batch

    def turn_batch_upright(self):
        batch = self._picked_batch("Turn upright")
        if not batch:
            return
        if not rotate.get_key():
            messagebox.showinfo("Turn upright", "This needs an Anthropic API key: add it in Settings ▸ Claude.")
            return
        n = len(self._batch_rows(batch))
        if messagebox.askokcancel("Turn upright", f"Claude will look at the {n} photo(s) of “{batch}” and turn "
                                  f"sideways or upside-down ones the right way up (about {n} cents).\n\n"
                                  "Photos with no clear up stay as they are."):
            self._turn_batch = batch
            self._run(self._do_turn_batch, f"Checking which way is up in “{batch}”…")

    def _batch_rows(self, batch):
        return [r for r in self._catalog().rows("status IN ('ready', 'filed')")
                if organizer.same_batch(r.get("batch"), batch)]

    def _do_turn_batch(self):
        cat, key = self._catalog(), rotate.get_key()
        rows = self._batch_rows(self._turn_batch)
        turned, problem = 0, None
        for start in range(0, len(rows), 8):  # a few at a time: progress, Cancel, and bounded memory
            self._stop_point()  # photos already turned stay turned (each is complete)
            chunk = rows[start:start + 8]
            self._progress(start / max(1, len(rows)))
            self.events.put(("note", f"Checking which way is up… {start}/{len(rows)}"))
            photos = [duplicates.read_image(cat.root / r["path"]) for r in chunk]
            have = [(r, img) for r, img in zip(chunk, photos) if img is not None]
            answers, problem = rotate.orient_all(key, [img for _, img in have])
            for (r, img), answer in zip(have, answers):
                if not answer:
                    continue
                degrees = answer["turn_clockwise"] if answer["confidence"] != "low" else 0
                if degrees:
                    path = cat.root / r["path"]
                    fmt = "TIFF" if path.suffix.lower() in (".tif", ".tiff") else "JPEG"
                    save_image(path, rotate.turn(img, degrees), int(r.get("dpi") or 600), fmt, self.s["jpeg_quality"])
                    turned += 1
                total = (int(r.get("rotated") or 0) + degrees) % 360
                fields = {"turn": 0} if degrees else {}  # the pixels are now upright: drop a turn set by hand
                cat.upsert(r["id"], rotated=total, orientation=f"{answer['confidence']}: {answer['reason']}",
                           meta="", **fields)  # meta="": organize() writes the photo's info into the new file again
        organizer.organize(cat)
        message = f"“{self._turn_batch}”: {turned} of {len(rows)} photo(s) turned upright."
        if problem:
            message += f" Some couldn't be checked: {problem}"
        return None, " ".join(t for t in (message, organizer.problem_text()) if t)

    def _renamed(self, old, new):
        cat = self._catalog()
        moved = organizer.rename_batch(cat, old, new)
        self.fix_batch_var.set(new)
        label = ""
        if self.batch_var.get().strip() == old:  # the next scans join the renamed batch
            self.batch_var.set(new)
            label = " The batch label for new scans was changed too."
        self.status.set(" ".join(t for t in (f"Batch “{old}” is now “{new}”: {moved} photo(s) moved.{label}",
                                             organizer.batch_report(cat, new), organizer.problem_text()) if t))
        self._refresh()

    def _show_batch_date(self):
        """Fill in the year and month already set for the chosen batch."""
        year, month = organizer.batch_date(self._catalog(), self.fix_batch_var.get().strip())
        self.fix_year_var.set(year)
        self.fix_month_var.set(MONTHS[int(month)] if month else MONTHS[0])

    def _set_year(self):
        batch, year = self.fix_batch_var.get().strip(), self.fix_year_var.get().strip()
        month = MONTHS.index(self.fix_month_var.get()) or None  # 0 = no month
        if not batch or (year and not re.fullmatch(r"(19|20)\d\d", year)) or (month and not year):
            messagebox.showinfo("Set year", "Pick a batch and type a year like 1994; the month is optional "
                                            "(empty year = automatic).")
            return
        cat = self._catalog()
        moved = organizer.set_batch_year(cat, batch, year, month)
        when = f"{self.fix_month_var.get()} {year}" if month else year
        self.status.set(" ".join(t for t in (
            f"Batch “{batch}” → {when or 'automatic year'}: {moved} photo(s) moved.",
            organizer.batch_report(cat, batch), organizer.problem_text()) if t))
        self._refresh()

    def _open_folder(self):
        os.startfile(self._root())

    def _open_catalog(self):
        cat = self._catalog()
        cat.export_csv()
        os.startfile(cat.root / "catalog.csv")

    def _save_settings(self):
        self.s.update(output=self.out_var.get(), front_dpi=int(self.dpi_var.get()), format=self.fmt_var.get(),
                      back_dpi=int(self.back_dpi_var.get()), jpeg_quality=int(self.quality_var.get()),
                      sharpen=self.sharpen_var.get(), restore_color=bool(self.restore_var.get()),
                      scan_backs=bool(self.backs_var.get()), batch=self.batch_var.get(),
                      notify=bool(self.notify_var.get()), flash=bool(self.flash_var.get()),
                      check_updates=bool(self.updates_var.get()), auto_rotate=bool(self.rotate_var.get()),
                      name_template=self.s["name_template"],
                      shortcuts_for=self.s["shortcuts_for"])
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.s, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---------- actions ----------
    def _ask_calibrate(self):
        if messagebox.askokcancel("Calibrate", "First, a one-time calibration.\n\nRemove everything from "
                                  "the glass, close the lid, then press OK.\n(Takes about 15 seconds.)"):
            self.calibrate()

    def reset_scanner(self):
        if self.busy:
            return
        self._run(self._do_reset_scanner, "Resetting the scanner… (allow it in the Windows prompt)")

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

    def _check_update(self):
        if updater.FROZEN:
            if self.updates_var.get() and time.time() - self._last_online >= ONLINE_CHECK_S:
                self._last_online = time.time()
                threading.Thread(target=self._ask_github, daemon=True).start()
        else:
            new = disk_version()
            if self.updates_var.get() and new and new != self._offered and _newer(new, __version__):
                self._offer(new)  # once per version, so Later keeps the banner away
        self.root.after(UPDATE_CHECK_MS, self._check_update)

    def _ask_github(self):
        try:
            release = updater.latest_release(__version__)
        except updater.UpdateError:
            return  # offline: try again next time
        if release and release[0] != self._offered:
            self.events.put(("release", release))

    def check_update_now(self):
        """Settings > Check now: (newer version or None, problem or None); shows the banner if there is one."""
        if updater.FROZEN:
            try:
                release = updater.latest_release(__version__)
            except updater.UpdateError as e:
                return None, str(e)
            if release:
                self._offer(release[0], release)
            return (release[0] if release else None), None
        new = disk_version()
        if new and _newer(new, __version__):
            self._offer(new)
            return new, None
        return None, None

    def check_updates_clicked(self):
        self.status.set("Checking for updates…")
        self.root.update_idletasks()
        new, problem = self.check_update_now()
        if problem:
            self.status.set("⚠ " + problem)
        elif new:
            button = "Update now" if updater.FROZEN else "Restart now"
            self.status.set(f"ReCollect {new} is available: press {button} at the top.")
        else:
            self.status.set(f"You're up to date (ReCollect {__version__}).")

    def _offer(self, new, release=None):
        self._release = release
        if release:  # ReCollect.exe: download it from GitHub
            self.update_text.set(f"ReCollect {new} is available (you're on {__version__}).")
            self.update_btn.config(text="Update now", command=self.update_now)
        else:  # from source: the new files are already in the folder
            self.update_text.set(f"ReCollect {new} is ready. Restart to use it (you're on {__version__}).")
            self.update_btn.config(text="Restart now", command=self.restart)
        self.update_box.pack(fill="x", pady=(0, 6), before=self._first_box)
        if new != self._offered:
            self._offered = new
            self._notify("Update available" if release else "Update ready",
                         f"ReCollect {new} is {'available' if release else 'ready'}. "
                         f"{'Update' if release else 'Restart'} when convenient.")

    def _can_close(self, what):
        """Not in the middle of a job or a bed; says why otherwise."""
        if self.busy:
            messagebox.showinfo(what, f"Wait for the current job to finish (or cancel it), then {what.lower()}.")
            return False
        if self.state == "back":
            messagebox.showinfo(what, "The prints on the glass are waiting for their backs. "
                                      f"Scan the backs (Space) or skip them (N) first, then {what.lower()}.")
            return False
        return True

    def update_now(self):
        if self._release and self._can_close("Update"):
            self._run(self._do_update, f"Downloading ReCollect {self._release[0]}…")

    def _do_update(self):
        new_dir = updater.download(self._release[1], self._progress)
        return None, "Update downloaded. ReCollect closes and opens again in a few seconds…", \
            lambda: self._apply_update(new_dir)

    def _apply_update(self, new_dir):
        try:
            updater.install_and_restart(new_dir)
        except updater.UpdateError as e:
            self.status.set("⚠ " + str(e))
            return
        self._save_settings()
        self.root.destroy()  # the helper copies the new files once this process has exited, then starts it

    def _whats_new(self, page=None):
        if page:  # the new release's notes on GitHub
            import webbrowser
            webbrowser.open(page)
            return
        try:
            os.startfile(APP_DIR / "CHANGELOG.md")
        except OSError:  # no app set up for .md files
            subprocess.Popen(["notepad.exe", str(APP_DIR / "CHANGELOG.md")])

    def _notify(self, title, text):
        if not (self.notify_var.get() or self.flash_var.get()):
            return
        try:
            notify.job_done(self.root, title, text, popup=self.notify_var.get(), flash_taskbar=self.flash_var.get())
        except Exception:  # a notification must never stop the app
            pass

    def restart(self):
        """Close and start again with the new version, but never in the middle of a job or a bed."""
        if not self._can_close("Restart"):
            return
        self._save_settings()
        self.restart_requested = True
        self.root.destroy()

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
        self._drop_bed()
        self._refresh()
        self.status.set("Last fronts deleted. Fix the placement, then press Space to scan again.")

    def cancel(self):
        """Stop the running job at its next safe point and throw away what it half did."""
        if not self.busy or self.cancelling.is_set():
            return
        self.cancelling.set()
        self._refresh()
        self.status.set("Cancelling… (a scan already under way finishes its pass first)")

    # progress (called from the job's thread; the bar is drawn in _poll)
    def _progress(self, value):
        self.events.put(("progress", value))

    def _scan_phase(self, start, end, seconds):
        """The bar creeps from start towards end over the expected scan time."""
        self.events.put(("phase", (start, end, seconds)))

    def _stop_point(self):
        if self.cancelling.is_set():
            raise Cancelled

    def _drop_bed(self):
        """Forget the fronts of the current bed: their files in the inbox and their catalogue rows."""
        out = self._out()
        for p in self.bed:
            for f in out.glob(f"{p['id']}*"):
                f.unlink()
            self._catalog().delete(p["id"])
        self.state, self.bed = "front", []

    def _run(self, fn, message):
        self._job, self._job_fn = JOB_TITLES.get(fn.__name__, "Done"), fn.__name__
        self.cancelling.clear()
        self._phase = None
        self.bar["value"] = 0
        self.busy = True
        self._refresh()
        self.status.set(message)
        threading.Thread(target=self._worker, args=(fn,), daemon=True).start()

    def _worker(self, fn):
        try:
            self.events.put(("done", fn()))
        except Cancelled:
            self.events.put(("cancelled", None))
        except (ScannerError, updater.UpdateError) as e:
            self.events.put(("error", str(e)))
        except Exception as e:  # keep the app alive whatever happens
            self.events.put(("error", f"{type(e).__name__}: {e}"))

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "release":  # found by the background check on GitHub
                    self._offer(payload[0], payload)
                    continue
                if kind == "note":  # progress message from a job that is still running
                    self.status.set(payload)
                    continue
                if kind == "progress":
                    self._phase, self.bar["value"] = None, payload
                    continue
                if kind == "phase":
                    a, b, secs = payload
                    self._phase = (a, b, time.time(), secs)
                    continue
                self.busy = False
                self._phase = None
                self.bar["value"] = 1.0 if kind == "done" else 0
                if kind == "cancelled":
                    self.cancelling.clear()
                    self._refresh()
                    self.status.set("Cancelled, nothing from it was kept. " + (
                        "Press Space to scan the backs again, or N to skip them." if self.state == "back" else
                        "Press Space (or P) when ready."))
                    continue
                self.cancelling.clear()
                if kind == "error":
                    self._refresh()
                    tip = " Still stuck? Settings ▸ Scanning ▸ Reset scanner." if self._job_fn in SCANNER_JOBS else ""
                    self.status.set("⚠ " + payload + tip)
                    self._notify("Scanner problem", payload)
                else:
                    view, message, *then = payload  # then: optional follow-up on the UI thread
                    self._refresh()
                    if view is not None:
                        self._show(view)
                    if message:
                        self.status.set(message)
                    self._notify(self._job, " ".join((message or "").split()))
                    for fn in then:
                        fn()
        except queue.Empty:
            pass
        if self._phase:  # estimated progress of the scan pass; never claims more than 95% of it
            a, b, t0, secs = self._phase
            self.bar["value"] = a + (b - a) * min((time.time() - t0) / secs, 0.95)
        self.root.after(100, self._poll)

    # ---------- work (runs in background thread) ----------
    def _do_calibrate(self):
        self._scan_phase(0, 1, scan_seconds(detect.WORK_DPI))
        img = scan_bed(detect.WORK_DPI)
        self._stop_point()
        cv2.imwrite(str(REFERENCE_FILE), img)
        self.reference = img
        return img, "Calibrated. Place prints face-down with ~1 cm gaps, close the lid, press Space."

    def _orient(self, photos):
        """Claude's answer per photo (None where there is none) and the first problem, if any."""
        if not self.rotate_var.get():
            return [None] * len(photos), None
        key = rotate.get_key()
        if not key:
            return [None] * len(photos), "no API key (Settings ▸ Claude)"
        self.events.put(("note", f"Checking which way is up ({len(photos)} photo{'s' if len(photos) > 1 else ''})…"))
        return rotate.orient_all(key, photos)

    def _do_reset_scanner(self):
        reset_scanner()
        return None, ("Scanner reset. Wait until its light stops flashing (up to a minute), then carry on. "
                      "If it keeps flashing, switch it off, wait 10 seconds and switch it on.")

    def _do_fronts(self):
        dpi = int(self.dpi_var.get())
        t = time.time()
        self._scan_phase(0, 0.6, scan_seconds(dpi))
        img = scan_bed(dpi)
        self._progress(0.6)
        self._stop_point()
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
        photos = []
        for i, r in enumerate(rects):
            self._stop_point()  # nothing saved yet
            self._progress(0.6 + 0.15 * i / len(rects))
            photos.append(enhance.process_front(detect.crop(img, r, dpi), dpi, self.s["sharpen"], self.s["restore_color"]))
        turns, turn_problem = self._orient(photos)
        self._stop_point()
        for i, (r, photo, answer) in enumerate(zip(rects, photos, turns)):
            if self.cancelling.is_set():
                self._drop_bed()  # undo the fronts of this bed saved so far
                raise Cancelled
            self._progress(0.8 + 0.2 * i / len(rects))
            pid = f"{first + i:05d}"
            turned = answer["turn_clockwise"] if answer and answer["confidence"] != "low" else 0
            photo = rotate.turn(photo, turned)
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
                       fingerprint=prints, duplicate_of=dup, rotated=turned,
                       orientation=f"{answer['confidence']}: {answer['reason']}" if answer else "")
            known.append((pid, prints))
            self.bed.append({"id": pid, "rect": r, "dpi": dpi, "dup": dup})
        if self.cancelling.is_set():  # pressed while the last photo was being saved
            self._drop_bed()
            raise Cancelled
        labels = [p["id"] + (f" = {p['dup']}" if p["dup"] else "") for p in self.bed]
        dups = [i for i, p in enumerate(self.bed) if p["dup"]]
        view = detect.draw_overlay(img, rects, labels, alert=dups)
        auto = [a["turn_clockwise"] if a and a["confidence"] != "low" else 0 for a in turns]
        pids = [p["id"] for p in self.bed]
        clickable = (lambda: self._set_clickable(img, rects, labels, dups, pids, auto))
        tip = "Click a photo to turn it (the arrow shows its top). "
        took = f"{time.time() - t:.0f}s" + (f", {stamps} with a camera date" if stamps else "")
        n_turned = sum(1 for a in turns if a and a["confidence"] != "low" and a["turn_clockwise"])
        if n_turned:
            took += f", {n_turned} turned upright"
        if turn_problem:
            took += f"; not turned: {turn_problem}"
        warn = ""
        if dups:
            warn = (f"⚠ {len(dups)} scanned before (red, “= original”): "
                    + ("press “Remove duplicates” to drop them. " if self.backs_var.get() else
                       "kept and tagged Duplicate; review them with Find duplicates. "))
        if self.backs_var.get():
            self.state = "back"
            return view, (f"Saved {len(rects)} fronts ({took}). {warn}Check the outlines: one box per print? "
                          f"{tip}Then flip each print IN THE SAME SPOT and press Space.  (N = skip backs, "
                          "Redo fronts = boxes wrong)"), clickable
        for p in self.bed:
            cat.upsert(p["id"], back_status="not scanned")
        summary = self._file_bed()
        self.bed = []
        return view, (f"Saved {len(rects)} photos ({took}). {warn}{summary}. {tip}Place the next prints and "
                      "press Space."), clickable

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
        self._scan_phase(0, 0.9, scan_seconds(PREVIEW_DPI))
        img = scan_bed(PREVIEW_DPI)
        self._progress(0.9)
        self._stop_point()
        rects, backs = self._prints_on_glass(img, PREVIEW_DPI)
        found = "Preview only, nothing saved. "
        if not rects:
            return detect.draw_overlay(img, []), found + "No prints found on the glass."
        found += f"Found {len(rects)} print{'s' if len(rects) > 1 else ''}."
        problems, bad = detect.check_placement(rects, img.shape, PREVIEW_DPI)
        if backs:
            problems.insert(0, f"{detect.print_names(sorted(backs))} {'is' if len(backs) == 1 else 'are'} "
                               "lying back-side down: turn face-down")
        view = detect.draw_overlay(img, rects, alert=bad | backs)
        if not problems:
            return view, f"{found}\n✓ Placement looks good. If that's the right number, press Space to scan."
        bullets = "\n".join(f"⚠ {p[0].upper()}{p[1:]}." for p in problems)
        big = max(max(r[1]) for r in rects) / PREVIEW_DPI * 2.54 >= 19  # 15x20 cm or larger
        tip = "\nTip: 15×20 prints fit only one per scan." if big and len(rects) > 1 and bad else ""
        then = ("Turn them over and press P to check again." if backs else
                "Move them and press P to check again, or press Space to scan anyway.")
        return view, f"{found}\n{bullets}{tip}\n{then}"

    def _do_find_duplicates(self):
        cat = self._catalog()
        rows = cat.rows("status IN ('ready', 'filed')")
        for i, r in enumerate(rows):  # photos scanned before this feature have no fingerprint yet
            self._stop_point()  # fingerprints already stored are kept: they are only a cache
            if i % 10 == 0:
                self._progress(0.5 * i / max(1, len(rows)))
            if not r.get("fingerprint"):
                if i % 10 == 0:
                    self.events.put(("note", f"Fingerprinting older photos… {i}/{len(rows)}"))
                img = duplicates.read_image(cat.root / r["path"])
                if img is not None:
                    r["fingerprint"] = duplicates.fingerprints(img)
                    cat.upsert(r["id"], fingerprint=r["fingerprint"])
        def progress(i, n):
            self._stop_point()
            self._progress(0.5 + 0.5 * i / max(1, n))
            self.events.put(("note", f"Comparing photos… {i}/{n}"))
        pairs = duplicates.find_all(rows, self._load_photo, progress)
        self._stop_point()
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
        self._scan_phase(0, 0.4, scan_seconds(dpi, share=min(1.0, region[3] / 11.7)))
        img = scan_bed(dpi, region=region, background=self.reference)
        self._progress(0.4)
        self._stop_point()  # cancelled: the bed stays waiting for its backs
        found = detect.find_photos(img, dpi, self.reference, refine=False)
        backs = detect.pair_backs(front_rects, found, dpi)
        out, ext, cat = self._out(), self._ext(), self._catalog()
        labels, kept, rows, saved = [], 0, [], []
        try:
            for i, (p, r) in enumerate(zip(self.bed, backs)):
                self._stop_point()
                self._progress(0.4 + 0.55 * i / len(backs))
                back = detect.crop(img, r, dpi)
                if detect.is_blank(back, dpi):
                    rows.append((p["id"], {"back_status": "blank"}))
                    labels.append(f"{p['id']} blank")
                    continue
                path = out / f"{p['id']}_back{ext}"
                save_image(path, back, dpi, self.s["format"], self.s["jpeg_quality"])
                saved.append(path)
                self.events.put(("note", f"Reading back of {p['id']}…"))
                personal, lab, paper = organizer.split_back_text(ocr.read_text(back))
                rows.append((p["id"], {"back_status": "saved", "back_path": self._rel(path),
                                       "back_text": personal, "back_lab": lab, "paper": paper}))
                labels.append(f"{p['id']} " + ("text" if personal else "back"))
                kept += 1
            self._stop_point()
        except Cancelled:  # the bed stays waiting for its backs, with nothing of this pass kept
            for path in saved:
                path.unlink(missing_ok=True)
            raise
        for pid, fields in rows:  # written only once the whole bed is read
            cat.upsert(pid, **fields)
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


class RenameBatch:
    """Small window: type a new name for a batch, or let Claude suggest one from its photos."""

    def __init__(self, app, batch):
        self.app, self.batch = app, batch
        self.win = tk.Toplevel(app.root)
        self.win.title("Rename batch")
        self.win.transient(app.root)
        self.win.resizable(False, False)
        box = ttk.Frame(self.win, padding=12)
        box.pack(fill="both")
        ttk.Label(box, text=f"New name for “{batch}”:").pack(anchor="w")
        self.name = tk.StringVar(value=batch)
        entry = ttk.Entry(box, textvariable=self.name, width=40)
        entry.pack(fill="x", pady=(4, 0))
        entry.focus_set()
        entry.select_range(0, "end")
        self.note = tk.StringVar(value="Folders, file names and tags follow the new name. "
                                       "An existing batch's name merges the two.")
        ttk.Label(box, textvariable=self.note, foreground="#666", wraplength=int(380 * app.ui),
                  justify="left").pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        self.suggest_btn = ttk.Button(row, text="Suggest with Claude", command=self._suggest)
        self.suggest_btn.pack(side="left")
        ttk.Button(row, text="Cancel", command=self.win.destroy).pack(side="right")
        ttk.Button(row, text="Rename", command=self._ok).pack(side="right", padx=4)
        self.win.bind("<Return>", lambda e: self._ok())
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self._answer = None

    def _ok(self):
        new = re.sub(r"\s+", " ", self.name.get()).strip()
        if not new or new == self.batch:
            self.win.destroy()
            return
        self.win.destroy()
        self.app._renamed(self.batch, new)

    def _suggest(self):
        key = rotate.get_key()
        if not key:
            self.note.set("Suggestions need an Anthropic API key: add it in Settings ▸ Claude.")
            return
        rows = [r for r in self.app._catalog().rows("status IN ('ready', 'filed', 'scanning')")
                if r.get("batch") == self.batch]
        step = max(1, len(rows) // namer.MAX_PHOTOS)  # spread over the batch
        picks = rows[::step][:namer.MAX_PHOTOS]
        backs = [r.get("back_text") for r in rows if r.get("back_text")][:30]
        self.suggest_btn.config(state="disabled")
        self.note.set(f"Asking Claude about {len(picks)} photo(s)…")

        def work():
            try:
                photos = [img for img in (self.app._load_photo(r["id"]) for r in picks) if img is not None]
                self._answer = namer.suggest(key, self.batch, photos, backs)
            except Exception as e:  # shown in the window, never fatal
                self._answer = e
        threading.Thread(target=work, daemon=True).start()
        self.win.after(200, self._wait)

    def _wait(self):
        if not self.win.winfo_exists():
            return
        if self._answer is None:
            self.win.after(200, self._wait)
            return
        answer, self._answer = self._answer, None
        self.suggest_btn.config(state="normal")
        if isinstance(answer, Exception):
            self.note.set(f"No suggestion: {answer}")
            return
        name, reason = answer
        self.name.set(name)
        self.note.set(f"Claude suggests this ({reason}). Edit it if you like, then press Rename.")


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


def _wait_for(pid, seconds=20):
    """After a restart: let the old copy close first, or it would count as 'already running'."""
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(seconds * 1000))
        ctypes.windll.kernel32.CloseHandle(handle)


def _relaunch():
    if updater.FROZEN:
        subprocess.Popen([sys.executable, "--after", str(os.getpid())], cwd=Path(sys.executable).parent)
        return
    exe = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen([str(exe if exe.exists() else sys.executable), str(APP_DIR / "photo_scanner.py"),
                      "--after", str(os.getpid())], cwd=APP_DIR)


def _first_start_of_exe():
    """ReCollect.exe: bring over settings and calibration from the source version (found through its
    shortcut), and point the shortcuts at the exe. Never stops the app from starting."""
    import shutil
    import install_shortcuts
    exe = str(Path(sys.executable))
    try:
        if not SETTINGS_FILE.exists():
            old = install_shortcuts.old_folder()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            for name in ("settings.json", "empty_bed.png"):
                if old and (old / name).exists():
                    shutil.copy2(old / name, DATA_DIR / name)
        s = load_settings()
        if s["shortcuts_for"] != exe:  # first start, or the folder was moved
            install_shortcuts.ensure_for_exe(exe)
            s["shortcuts_for"] = exe
            SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception:
        pass


def _selftest():
    """ReCollect.exe --selftest: everything the app loads later is in the bundle (run by the build)."""
    import install_shortcuts  # noqa: F401
    updater.latest_release("0.0.0")  # HTTPS to GitHub works with the bundled certificate check
    import win32con, win32gui, win32event, win32process  # noqa: F401
    from winrt.windows.data.xml.dom import XmlDocument  # noqa: F401
    from winrt.windows.ui.notifications import ToastNotificationManager  # noqa: F401
    from win32com.shell import shell  # noqa: F401
    import win32com.client  # noqa: F401
    import win32cred  # noqa: F401
    import anthropic
    anthropic.Anthropic(api_key="test").beta.messages  # loads the SDK's lazily imported parts
    assert ICON_FILE.exists() and (APP_DIR / "ReCollect.png").exists() and (APP_DIR / "CHANGELOG.md").exists()


def _already_running():
    """True (and the open window brought to the front) if ReCollect is already running."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "ReCollect.PhotoScans.single")  # held until this process exits
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    import win32con, win32gui
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h).startswith("ReCollect ") else None, None)
    for h in found:
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(h)
        except Exception:
            pass
    return True


if __name__ == "__main__":
    try:  # HTTPS (GitHub, Claude) trusts what Windows trusts, like the browser does
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    if "--selftest" in sys.argv:
        try:
            _selftest()
        except BaseException as e:
            (Path(tempfile.gettempdir()) / "recollect-selftest.txt").write_text(repr(e), encoding="utf-8")
            os._exit(1)
        os._exit(0)
    if "--after" in sys.argv:
        _wait_for(int(sys.argv[sys.argv.index("--after") + 1]))
    if _already_running():
        raise SystemExit
    if updater.FROZEN:
        _first_start_of_exe()
    _own_taskbar_entry()
    _dpi_aware()
    root = tk.Tk()
    app = App(root)
    root.mainloop()
    if app.restart_requested:
        _relaunch()
