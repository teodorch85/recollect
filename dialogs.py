"""Small windows of the main window: renaming a batch, and reviewing the duplicates found."""
import re
import threading
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

import duplicates
import library
import namer
import organizer
import rotate


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
                photos = [img for img in (library.load_photo(self.app._catalog(), r["id"]) for r in picks) if img is not None]
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
