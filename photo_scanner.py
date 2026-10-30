"""ReCollect: scan several prints at once on the V600, then their backs, and file them.

Workflow per bed:
  1. Place prints with ~1 cm gaps, close the lid, press Space  -> fronts are split, straightened, saved;
     an orange camera date printed in a corner ('94 7 15) is read too.
  2. Flip every print over in the same spot, press Space       -> backs are matched by position and
     read with Windows OCR; blank backs are discarded. (Press N instead to skip backs for this bed.)
  3. Photos are filed at once into <YEAR>/<Batch>/ by simple rules (see organizer.py), with the back
     text in the filename and everything recorded in photos.db / catalog.csv.
"""
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

import bed
import config
import detect
import dialogs
import library
import notify
import organizer
import naming
import review
import rotate
import settings_window
import updater
import winapp
from config import APP_DIR, DEFAULTS, ICON_FILE, REFERENCE_FILE
from library import Cancelled
from scanner import ScannerError, reset_scanner, scan_bed
from version import __version__

# title of the Windows notification when each job finishes
JOB_TITLES = {"_do_fronts": "Fronts scanned", "_do_backs": "Backs read, photos filed", "_do_preview": "Preview ready",
              "_do_find_duplicates": "Duplicate check finished", "_do_calibrate": "Scanner calibrated",
              "_do_reset_scanner": "Scanner reset", "_do_update": "Update downloaded",
              "_do_turn_batch": "Photos turned upright", "_do_refile": "Library refiled",
              "_do_split": "Photo split"}
SCANNER_JOBS = ("_do_fronts", "_do_backs", "_do_preview", "_do_calibrate")
UPDATE_CHECK_MS = 60_000  # how often to look for a newer version in this folder (from source)
ONLINE_CHECK_S = 6 * 3600  # how often ReCollect.exe asks GitHub for a new release
MONTHS = ["—", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PREVIEW_SIDE = 1400  # longest side of the bed picture in the preview (as detect.draw_overlay)


class AppJob(library.Job):
    """Progress of a background job goes to the main window's bar and status line; Cancel stops it."""

    def __init__(self, app):
        self.app = app

    def progress(self, value):
        self.app.events.put(("progress", value))

    def phase(self, start, end, seconds):
        self.app.events.put(("phase", (start, end, seconds)))

    def note(self, text):
        self.app.events.put(("note", text))

    def cancelled(self):
        return self.app.cancelling.is_set()


class App:
    def __init__(self, root):
        self.root = root
        self.s = config.load_settings()
        self.events = queue.Queue()
        self.busy = False
        self.cancelling = threading.Event()
        self.job = AppJob(self)
        self.state = "front"          # "front" -> waiting for fronts, "back" -> waiting for backs
        self.bed = []                 # [{id, rect, dpi}] for prints on the glass right now
        self.reference = cv2.imread(str(REFERENCE_FILE)) if REFERENCE_FILE.exists() else None
        self._cat = None
        self._review = None  # the Review photos window, while open
        self.restart_requested = False
        self._offered = None  # version already offered, so it is announced once
        self._release = None  # (version, zip url, page) of a newer release on GitHub
        self._last_online = time.time() - ONLINE_CHECK_S + 60  # first look a minute after start
        self.app_dir = APP_DIR
        self._settings_win = None

        root.title(f"ReCollect {__version__}")
        root.iconbitmap(default=str(ICON_FILE))  # window, title bar and taskbar
        self.ui = root.winfo_fpixels("1i") / 96  # display scaling: 1.25 at 125%
        x, y, w, h = winapp.work_area()
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
        tools = ttk.Frame(org)
        tools.grid(row=4, column=0, columnspan=4, sticky="we", pady=(4, 0))
        ttk.Button(tools, text="Review photos…", command=self.open_review).pack(side="left", fill="x", expand=True)
        ttk.Button(tools, text="Find duplicates…", command=self.find_duplicates).pack(side="left", fill="x",
                                                                                   expand=True, padx=(4, 0))
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
        self.canvas.bind("<Button-1>", lambda e: self._click_view(e, 90))  # the photo turns clockwise
        self.canvas.bind("<Button-3>", lambda e: self._click_view(e, -90))
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
    def _set_clickable(self, img, rects, labels, alert, pids):
        """Make the fronts on show clickable. Keeps a preview-sized copy of the bed, not the full scan."""
        f = PREVIEW_SIDE / max(img.shape[:2])
        small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        rects = [((cx * f, cy * f), (w * f, h * f), a) for (cx, cy), (w, h), a in rects]
        self._clickable = {"img": small, "rects": rects, "labels": labels, "alert": alert, "pids": pids,
                           "root": self._catalog().root}
        self._draw_clickable()

    def _clickable_rows(self):
        """Catalogue rows of the photos on show (None where deleted), or None if the library changed."""
        c = self._clickable
        if self._catalog().root != c["root"]:  # another photos folder: these ids mean other photos there
            return None
        rows = {r["id"]: r for r in self._catalog().rows(f"id IN ({','.join('?' * len(c['pids']))})", c["pids"])}
        return [rows.get(pid) for pid in c["pids"]]

    def _draw_clickable(self):
        """Redraw the fronts with an arrow to each photo's top, from what the catalogue says now."""
        c = self._clickable
        rows = self._clickable_rows()
        if rows is None:
            self._clickable = None
            return
        ups, labels = [], []
        for r, rect, label in zip(rows, c["rects"], c["labels"]):
            turn = int((r or {}).get("turn") or 0)
            ups.append(detect.top_direction(rect, int((r or {}).get("rotated") or 0) + turn))
            labels.append(label + (f" +{turn}" if turn else ""))
        self._view = Image.fromarray(cv2.cvtColor(detect.draw_overlay(c["img"], c["rects"], labels, alert=c["alert"],
                                                                      ups=ups, max_side=PREVIEW_SIDE),
                                                  cv2.COLOR_BGR2RGB))
        self._render()

    def _click_view(self, event, delta):
        """Turn the clicked photo a quarter turn: only its EXIF orientation changes, not its pixels."""
        c = self._clickable
        if not c or self.busy or not getattr(self, "_photo", None):
            return
        shown_w, shown_h = self._photo.width(), self._photo.height()
        x = event.x - (self.canvas.winfo_width() - shown_w) / 2
        y = event.y - (self.canvas.winfo_height() - shown_h) / 2
        to_small = c["img"].shape[1] / shown_w  # preview pixel -> stored preview pixel (same aspect ratio)
        i = detect.hit(c["rects"], x * to_small, y * to_small)
        rows = self._clickable_rows()
        if i is None or rows is None or rows[i] is None:  # outside, another library, or deleted (Redo fronts)
            if rows is None:
                self._clickable = None
            return
        message = self.turn_photo(c["pids"][i], delta)
        then = " Then flip the prints and press Space." if self.state == "back" else ""
        self.status.set(message.replace("Only the orientation tag changes.",
                                        "Click again to keep turning, right-click to turn back (the arrow shows "
                                        "its top). Only the orientation tag changes." + then))

    def turn_photo(self, pid, delta):
        message = library.turn_photo(self._catalog(), pid, delta)
        self._refresh_clickable()
        return message

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

    def _catalog(self):
        root = self._root()
        if self._cat is None or self._cat.root != root:
            self._cat = organizer.Catalog(root)
            organizer.finish_unfinished(self._cat, keep={p["id"] for p in self.bed})
            organizer.organize(self._cat)  # files stuck photos, applies new naming rules; quick when nothing changed
        return self._cat

    def _fill_batches(self):
        self.fix_batch["values"] = organizer.batches(self._catalog())

    def rename_batch(self):
        batch = self._picked_batch("Rename batch")
        if batch:
            dialogs.RenameBatch(self, batch)

    def apply_template(self, template):
        """Use a new naming template and refile the whole library to match."""
        organizer.template = naming.parse(template)
        self.s["name_template"] = template
        self._save_settings()
        self._run(self._do_refile, "Renaming and moving photos to the new template…")

    def _do_refile(self):
        return None, library.refile(self._catalog())

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
        n = len(library.batch_rows(self._catalog(), batch))
        if messagebox.askokcancel("Turn upright", f"Claude will look at the {n} photo(s) of “{batch}” and turn "
                                  f"sideways or upside-down ones the right way up (about {n} cents).\n\n"
                                  "Photos with no clear up stay as they are."):
            self._turn_batch = batch
            self._run(self._do_turn_batch, f"Checking which way is up in “{batch}”…")

    def _do_turn_batch(self):
        message = library.turn_upright(self._catalog(), self._turn_batch, rotate.get_key(), self.s["jpeg_quality"],
                                       self.job)
        return None, message, self._refresh_clickable

    def open_review(self):
        if self._review and self._review.win.winfo_exists():
            self._review.win.lift()
            return
        batch = self.fix_batch_var.get().strip()
        known = [b for b in organizer.batches(self._catalog()) if organizer.same_batch(b, batch)]
        self._review = review.Review(self, known[0] if known else None)

    def delete_photo(self, pid):
        organizer.remove_photo(self._catalog(), pid)
        self._refresh()
        self._refresh_clickable()
        return f"{pid} is in the Recycle Bin."

    def start_split(self, row, glass, back, line):
        self._split = (row, glass, back, line)
        self._run(self._do_split, f"Splitting {row['id']} into two photos…")

    def _do_split(self):
        row, glass, back, line = self._split
        message = library.split_photo(self._catalog(), row, glass, back, line, self.s["jpeg_quality"], self.job)
        return None, message, self._refresh_clickable, lambda: self._review and self._review.changed(message)

    def _refresh_clickable(self):
        if self._clickable:
            self._draw_clickable()

    def _renamed(self, old, new):
        cat = self._catalog()
        moved = organizer.rename_batch(cat, old, new)
        self.fix_batch_var.set(new)
        label = ""
        if self.batch_var.get().strip() == old:  # the next scans join the renamed batch
            self.batch_var.set(new)
            label = " The batch label for new scans was changed too."
        self._refresh()  # before the message: it resets the status line
        self.status.set(" ".join(t for t in (f"Batch “{old}” is now “{new}”: {moved} photo(s) moved.{label}",
                                             organizer.batch_report(cat, new), organizer.problem_text()) if t))

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
        self._refresh()  # before the message: it resets the status line
        self.status.set(" ".join(t for t in (
            f"Batch “{batch}” → {when or 'automatic year'}: {moved} photo(s) moved.",
            organizer.batch_report(cat, batch), organizer.problem_text()) if t))

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
        config.save_settings(self.s)

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
            new = config.disk_version()
            if self.updates_var.get() and new and new != self._offered and config.newer(new, __version__):
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
        new = config.disk_version()
        if new and config.newer(new, __version__):
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
        new_dir = updater.download(self._release[1], self.job.progress)
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
        summary = library.file_photos(self._catalog(), [p["id"] for p in self.bed])
        self.state, self.bed = "front", []
        self._refresh()
        self.status.set(summary + ". Place the next prints and press Space.")

    def redo_fronts(self):
        """Delete the fronts just saved (e.g. two prints merged) so the bed can be rescanned."""
        if self.busy or self.state != "back":
            return
        bed.drop(self._catalog(), self.bed)
        self.state, self.bed = "front", []
        self._refresh()
        self.status.set("Last fronts deleted. Fix the placement, then press Space to scan again.")

    def cancel(self):
        """Stop the running job at its next safe point and throw away what it half did."""
        if not self.busy or self.cancelling.is_set():
            return
        self.cancelling.set()
        self._refresh()
        self.status.set("Cancelling… (a scan already under way finishes its pass first)")

    def _run(self, fn, message):
        self._job, self._job_fn = JOB_TITLES.get(fn.__name__, "Done"), fn.__name__
        self.cancelling.clear()
        self._phase = None
        self.bar["value"] = 0
        self.busy = True
        self._refresh()
        self.status.set(message)
        if self._review:
            self._review.working(message)
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
                    if self._review:
                        self._review.working(payload)
                    continue
                if kind == "progress":
                    self._phase, self.bar["value"] = None, payload
                    continue
                if kind == "phase":
                    a, b, secs = payload
                    self._phase = (a, b, time.time(), secs)
                    continue
                self.busy = False
                if self._review:  # the job's own message follows (Review.changed) for a split
                    self._review.idle("Cancelled." if kind == "cancelled" else
                                      "⚠ " + payload if kind == "error" else "Done.")
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
        self.job.phase(0, 1, bed.scan_seconds(detect.WORK_DPI))
        img = scan_bed(detect.WORK_DPI)
        self.job.stop_point()
        cv2.imwrite(str(REFERENCE_FILE), img)
        self.reference = img
        return img, "Calibrated. Place prints face-down with ~1 cm gaps, close the lid, press Space."

    def _orient(self, photos):
        """Claude's answer per photo (None where there is none) and the first problem, if any."""
        if not self.rotate_var.get():
            return bed.no_answers(photos)
        key = rotate.get_key()
        if not key:
            return [None] * len(photos), "no API key (Settings ▸ Claude)"
        self.job.note(f"Checking which way is up ({len(photos)} photo{'s' if len(photos) > 1 else ''})…")
        return rotate.orient_all(key, photos)

    def _do_reset_scanner(self):
        reset_scanner()
        return None, ("Scanner reset. Wait until its light stops flashing (up to a minute), then carry on. "
                      "If it keeps flashing, switch it off, wait 10 seconds and switch it on.")

    def _do_fronts(self):
        dpi, started = int(self.dpi_var.get()), time.time()
        self.job.phase(0, 0.6, bed.scan_seconds(dpi))
        img = scan_bed(dpi)
        self.job.progress(0.6)
        self.job.stop_point()
        done = bed.save_fronts(self._catalog(), img, dpi, self.reference, self.s, self.batch_var.get().strip(),
                               self.backs_var.get(), self._orient, self.job, started)
        if done.bed:
            self.state, self.bed = "back", done.bed
        then = [lambda: self._set_clickable(img, *done.shown)] if done.shown else []
        return (done.view, done.message, *then)

    def _do_preview(self):
        self.job.phase(0, 0.9, bed.scan_seconds(bed.PREVIEW_DPI))
        img = scan_bed(bed.PREVIEW_DPI)
        self.job.progress(0.9)
        self.job.stop_point()
        return bed.check_placement(img, self.reference)

    def _do_find_duplicates(self):
        pairs, message = library.find_duplicates(self._catalog(), self.job)
        return (None, message, lambda: dialogs.DuplicateReview(self, pairs)) if pairs else (None, message)

    def _do_backs(self):
        dpi = int(self.s["back_dpi"])
        region = bed.back_region(self.bed, dpi)
        self.job.phase(0, 0.4, bed.scan_seconds(dpi, share=min(1.0, region[3] / 11.7)))
        img = scan_bed(dpi, region=region, background=self.reference)
        self.job.progress(0.4)
        self.job.stop_point()  # cancelled: the bed stays waiting for its backs
        view, message = bed.save_backs(self._catalog(), img, dpi, self.bed, self.reference, self.s, self.job)
        self.state, self.bed = "front", []
        return view, message


if __name__ == "__main__":
    try:  # HTTPS (GitHub, Claude) trusts what Windows trusts, like the browser does
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    if "--selftest" in sys.argv:
        try:
            winapp.selftest()
        except BaseException as e:
            (Path(tempfile.gettempdir()) / "recollect-selftest.txt").write_text(repr(e), encoding="utf-8")
            os._exit(1)
        os._exit(0)
    if "--after" in sys.argv:
        winapp.wait_for(int(sys.argv[sys.argv.index("--after") + 1]))
    if winapp.already_running():
        raise SystemExit
    if updater.FROZEN:
        winapp.first_start_of_exe()
    winapp.own_taskbar_entry()
    winapp.dpi_aware()
    root = tk.Tk()
    app = App(root)
    root.mainloop()
    if app.restart_requested:
        winapp.relaunch()
