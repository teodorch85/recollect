"""Review photos: every photo of a batch at a glance, with its back; turn, split or delete them there."""
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

import organizer
import rotate
import split

ALL = "All photos"
COLUMNS = 4
REDUCED = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4,
           8: cv2.IMREAD_REDUCED_COLOR_8}


def read(path, reduce=1):
    """The pixels as stored (no EXIF turn), optionally decoded at 1/2, 1/4 or 1/8 size (much quicker)."""
    try:
        data = np.fromfile(str(path), np.uint8)  # np.fromfile: Greek folder names
    except OSError:
        return None
    return cv2.imdecode(data, REDUCED[reduce] | cv2.IMREAD_IGNORE_ORIENTATION) if data.size else None


def _fit(bgr, w, h):
    f = min(w / bgr.shape[1], h / bgr.shape[0])
    small = cv2.resize(bgr, (max(1, round(bgr.shape[1] * f)), max(1, round(bgr.shape[0] * f))),
                       interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))


def _turn(row):
    turn = str(row.get("turn") or "0")
    return int(turn) if turn.isdigit() else 0


class Review:
    """Thumbnails of a batch on the left; the chosen photo large on the right, with its back and info."""

    def __init__(self, app, batch=None):
        self.app, self.ui = app, app.ui
        self.rows, self.cells, self.i, self.gen = [], [], None, 0
        self.thumbs = queue.Queue()
        self.win = tk.Toplevel(app.root)
        self.win.title("Review photos")
        x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
        self.win.geometry(f"{int(1250 * self.ui)}x{int(820 * self.ui)}+{x + 20}+{y + 20}")
        self.thumb = int(130 * self.ui)
        self.blank = ImageTk.PhotoImage(Image.new("RGB", (self.thumb, self.thumb), "#dddddd"))

        top = ttk.Frame(self.win, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Batch:").pack(side="left")
        self.batch = tk.StringVar(value=batch or ALL)
        box = ttk.Combobox(top, textvariable=self.batch, state="readonly", width=36,
                           values=[ALL] + organizer.batches(app._catalog()))
        box.pack(side="left", padx=6)
        box.bind("<<ComboboxSelected>>", lambda e: self.load())
        self.count = tk.StringVar()
        ttk.Label(top, textvariable=self.count, foreground="#666").pack(side="left", padx=6)
        ttk.Button(top, text="Close", command=self.close).pack(side="right")

        body = ttk.Frame(self.win, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.grid = tk.Canvas(left, width=COLUMNS * (self.thumb + 12) + 4, highlightthickness=0, background="#f3f3f3")
        bar = ttk.Scrollbar(left, orient="vertical", command=self.grid.yview)
        self.grid.configure(yscrollcommand=bar.set)
        self.grid.pack(side="left", fill="y")
        bar.pack(side="left", fill="y")
        self.inner = tk.Frame(self.grid, background="#f3f3f3")
        self.grid.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.grid.configure(scrollregion=self.grid.bbox("all")))
        self.win.bind("<MouseWheel>", self._wheel)

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        self.big = tk.Label(right, background="#333")
        self.big.pack(fill="both", expand=True)
        below = ttk.Frame(right)
        below.pack(fill="x", pady=(8, 0))
        frame = tk.Frame(below, background="#333", width=int(220 * self.ui), height=int(150 * self.ui))
        frame.pack(side="left")
        frame.pack_propagate(False)  # a fixed size, with or without a back to show
        self.back = tk.Label(frame, background="#333", foreground="#aaa")
        self.back.pack(fill="both", expand=True)
        self.info = tk.StringVar()
        ttk.Label(below, textvariable=self.info, justify="left", wraplength=int(520 * self.ui),
                  padding=(10, 0)).pack(side="left", anchor="n", fill="x")
        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(8, 0))
        self.buttons = [
            ttk.Button(actions, text="⟲ Turn left  [L]", command=lambda: self.turn(-90)),
            ttk.Button(actions, text="Turn right ⟳  [R]", command=lambda: self.turn(90)),
            ttk.Button(actions, text="Split into two…  [S]", command=self.split),
            ttk.Button(actions, text="Delete…  [Del]", command=self.delete),
            ttk.Button(actions, text="Show in folder", command=self.show_in_folder),
        ]
        for b in self.buttons:
            b.pack(side="left", padx=(0, 4))
        status = ttk.Frame(right)
        status.pack(fill="x", pady=(6, 0))
        self.spinner = ttk.Progressbar(status, mode="indeterminate", length=int(120 * self.ui))
        self.note = tk.StringVar(value="←/→ previous and next photo.")
        ttk.Label(status, textvariable=self.note, foreground="#555", wraplength=int(620 * self.ui),
                  justify="left").pack(side="left", anchor="w")

        for key, fn in (("<Left>", lambda: self.go(-1)), ("<Right>", lambda: self.go(1)),
                        ("<Up>", lambda: self.go(-COLUMNS)), ("<Down>", lambda: self.go(COLUMNS)),
                        ("<l>", lambda: self.turn(-90)), ("<r>", lambda: self.turn(90)),
                        ("<s>", self.split), ("<Delete>", self.delete), ("<Escape>", self.close)):
            self.win.bind(key, lambda e, f=fn: f())
        self.big.bind("<Configure>", lambda e: self._show_big())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.load()
        self._poll()

    # ---------- the list ----------
    def load(self, keep=None):
        """(Re)read the batch's photos; keep: id to stay on (or the photo after it, if it's gone)."""
        cat = self.app._catalog()
        batch = self.batch.get()
        self.rows = [r for r in cat.rows("status IN ('ready', 'filed')")
                     if batch == ALL or organizer.same_batch(r.get("batch"), batch)]
        self.count.set(f"{len(self.rows)} photo{'s' if len(self.rows) != 1 else ''}")
        for child in self.inner.winfo_children():
            child.destroy()
        self.cells = []
        for n, r in enumerate(self.rows):
            cell = tk.Frame(self.inner, background="#f3f3f3", padx=3, pady=3)
            cell.grid(row=n // COLUMNS, column=n % COLUMNS, padx=1, pady=1)
            pic = tk.Label(cell, image=self.blank, background="#f3f3f3")
            pic.pack()
            cap = tk.Label(cell, text=r["id"], background="#f3f3f3")
            cap.pack()
            for w in (cell, pic, cap):
                w.bind("<Button-1>", lambda e, k=n: self.select(k))
            self.cells.append({"cell": cell, "pic": pic, "cap": cap, "img": None})
        self.grid.yview_moveto(0)
        self.gen += 1  # thumbnails still coming for the old list are dropped
        threading.Thread(target=self._load_thumbs, args=(self.gen, list(self.rows), cat.root), daemon=True).start()
        ids = [r["id"] for r in self.rows]
        i = 0
        if keep is not None:
            i = next((k for k, pid in enumerate(ids) if pid >= keep), len(ids) - 1)
        self.i = None
        if self.rows:
            self.select(max(i, 0))
        else:
            self.big.config(image="", text="No photos in this batch.", foreground="#aaa")
            self.back.config(image="", text="")
            self.info.set("")

    def _load_thumbs(self, gen, rows, root):
        for n, r in enumerate(rows):
            if gen != self.gen:
                return
            img = read(root / (r.get("path") or ""), 8)
            if img is not None:
                self.thumbs.put((gen, n, _fit(rotate.turn(img, _turn(r)), self.thumb, self.thumb)))

    def _poll(self):
        try:
            while True:
                gen, n, img = self.thumbs.get_nowait()
                if gen == self.gen and n < len(self.cells):
                    self._set_thumb(n, img)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(60, self._poll)

    def _set_thumb(self, n, img):
        c = self.cells[n]
        square = Image.new("RGB", (self.thumb, self.thumb), "#f3f3f3")  # same size cells, whatever the shape
        square.paste(img, ((self.thumb - img.width) // 2, (self.thumb - img.height) // 2))
        c["img"] = ImageTk.PhotoImage(square)
        c["pic"].config(image=c["img"])

    def _wheel(self, event):
        self.grid.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ---------- the chosen photo ----------
    def select(self, i):
        if not self.rows:
            return
        i = min(max(i, 0), len(self.rows) - 1)
        for k in (self.i, i):
            if k is not None and k < len(self.cells):
                on = k == i
                for part in ("cell", "cap"):
                    self.cells[k][part].config(background="#2f7de1" if on else "#f3f3f3")
                self.cells[k]["cap"].config(foreground="white" if on else "black")
        self.i = i
        row = self.rows[i] = self._fresh(self.rows[i])
        root = self.app._catalog().root
        self._front = read(root / (row.get("path") or ""), 2)
        self._back = read(root / row["back_path"], 4) if row.get("back_path") else None
        self._show_big()
        if self._back is not None:
            self._back_img = ImageTk.PhotoImage(_fit(self._back, int(220 * self.ui), int(150 * self.ui)))
            self.back.config(image=self._back_img, text="")
        else:
            self._back_img = None
            self.back.config(image="", text="back blank" if row.get("back_status") == "blank" else "no back scanned")
        year = row.get("year") or "unknown year"
        source = f" ({row['year_source']})" if row.get("year") and row.get("year_source") else ""
        lines = [f"{row['id']}  ·  {row.get('batch') or 'no batch'}  ·  {year}{source}",
                 row.get("path") or "",
                 " · ".join(t for t in (row.get("print_format") and f"{row['print_format']} print",
                                        row.get("paper"), row.get("date_stamp") and f"camera date {row['date_stamp']}",
                                        row.get("duplicate_of") and f"duplicate of {row['duplicate_of']}") if t)]
        if row.get("back_text"):
            lines.append(f"Back: {row['back_text']}")
        self.info.set("\n".join(t for t in lines if t))
        self._scroll_to(i)

    def _fresh(self, row):
        rows = self.app._catalog().rows("id = ?", (row["id"],))
        return rows[0] if rows else row

    def _show_big(self):
        if self.i is None or not self.rows:
            return
        w, h = self.big.winfo_width() - 8, self.big.winfo_height() - 8
        if self._front is None:
            self.big.config(image="", text=f"Can't read {self.rows[self.i].get('path')}", foreground="#aaa")
            return
        if w < 50 or h < 50:
            return
        self._big_img = ImageTk.PhotoImage(_fit(rotate.turn(self._front, _turn(self.rows[self.i])), w, h))
        self.big.config(image=self._big_img, text="")

    def _scroll_to(self, i):
        self.win.update_idletasks()
        cell = self.cells[i]["cell"]
        total = max(1, self.inner.winfo_height())
        top, bottom = cell.winfo_y() / total, (cell.winfo_y() + cell.winfo_height()) / total
        first, last = self.grid.yview()
        if top < first:
            self.grid.yview_moveto(top)
        elif bottom > last:
            self.grid.yview_moveto(bottom - (last - first))

    def go(self, step):
        if self.i is not None:
            self.select(self.i + step)

    # ---------- changes ----------
    def _ready(self):
        if self.i is None or not self.rows:
            return False
        if self.app.busy:
            self.note.set("Wait for the current job in the main window to finish.")
            return False
        return True

    def turn(self, delta):
        if not self._ready():
            return
        self.note.set(self.app.turn_photo(self.rows[self.i]["id"], delta))
        self.rows[self.i] = self._fresh(self.rows[self.i])
        self._show_big()
        if self._front is not None:
            img = rotate.turn(cv2.resize(self._front, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA),
                              _turn(self.rows[self.i]))
            self._set_thumb(self.i, _fit(img, self.thumb, self.thumb))

    def split(self):
        if not self._ready():
            return
        row = self.rows[self.i]
        root = self.app._catalog().root
        front = read(root / row["path"])
        if front is None:
            self.note.set(f"Can't read {row['path']}.")
            return
        back = read(root / row["back_path"]) if row.get("back_path") else None
        # as it lay on the glass: the back was cut from the same place, so one line cuts both
        glass = rotate.turn(front, (360 - int(row.get("rotated") or 0)) % 360)
        SplitPhoto(self.app, row, glass, back, parent=self.win)

    def delete(self):
        if not self._ready():
            return
        pid = self.rows[self.i]["id"]
        if messagebox.askyesno("Delete photo", f"Send photo {pid} and its back scan to the Recycle Bin?",
                               parent=self.win):
            self.note.set(self.app.delete_photo(pid))
            self.load(keep=pid)

    def show_in_folder(self):
        if self.i is None or not self.rows:
            return
        path = self.app._catalog().root / self.rows[self.i]["path"]
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])

    def working(self, message):
        """A job runs in the main window (e.g. a split): say so, and wait for it."""
        if not self.win.winfo_exists():
            return
        if not self.spinner.winfo_ismapped():
            self.spinner.pack(side="left", padx=(0, 8), before=self.spinner.master.winfo_children()[-1])
            self.spinner.start(15)
            for b in self.buttons[:4]:
                b.state(["disabled"])
            self.win.config(cursor="watch")
        self.note.set(message)

    def idle(self, message):
        if not self.win.winfo_exists():
            return
        self.spinner.stop()
        self.spinner.pack_forget()
        for b in self.buttons[:4]:
            b.state(["!disabled"])
        self.win.config(cursor="")
        self.note.set(message)

    def changed(self, note=""):
        """Called by the app after it changed photos (e.g. a split): show them as they are now."""
        if self.win.winfo_exists():
            keep = self.rows[self.i]["id"] if self.i is not None and self.rows else None
            self.load(keep=keep)
            if note:
                self.note.set(note)

    def close(self):
        self.gen += 1
        self.app._review = None
        self.win.destroy()


class SplitPhoto:
    """Window: where to cut a scan that holds two prints. Shows the line it found and, in green, the two
    photos that would be saved. Click near the gap to look there, or drag along it to draw the cut."""

    def __init__(self, app, row, glass, back, parent=None):
        self.app, self.row, self.glass, self.back = app, row, glass, back
        self.dpi = int(row.get("dpi") or 600)
        self.win = tk.Toplevel(parent or app.root)
        self.win.title(f"Split photo {row['id']}")
        self.win.transient(parent or app.root)
        self.win.resizable(False, False)
        side = int((440 if back is not None else 620) * app.ui)
        pics = ttk.Frame(self.win, padding=8)
        pics.pack()
        self.pics = []
        for img, label in ((glass, "Front"), (back, "Back")):
            if img is None:
                continue
            col = ttk.Frame(pics)
            col.pack(side="left", padx=4)
            ttk.Label(col, text=label).pack(anchor="w")
            photo = _fit(img, side, side)
            canvas = tk.Canvas(col, width=photo.width, height=photo.height, highlightthickness=0, cursor="crosshair")
            canvas.pack()
            tk_img = ImageTk.PhotoImage(photo)
            canvas.create_image(0, 0, anchor="nw", image=tk_img)
            canvas.bind("<ButtonPress-1>", lambda e: self._press(e))
            canvas.bind("<B1-Motion>", lambda e, c=canvas: self._drag(e, c))
            canvas.bind("<ButtonRelease-1>", lambda e, c=canvas: self._release(e, c))
            self.pics.append((canvas, tk_img))
        note = ("Its back is stored too: both are cut along the same line." if back is not None else
                "No back is stored for this photo (it was blank or not scanned): only the front is cut.")
        ttk.Label(self.win, text="As the prints lay on the glass. Red: the cut. Green: the two photos that will be "
                                 "saved. Not right? Click in the gap between the prints, or drag along it to draw "
                                 "the cut yourself.\n" + note,
                  wraplength=int(900 * app.ui), justify="left", padding=(8, 0)).pack(anchor="w")
        bar = ttk.Frame(self.win, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Find the gap again", command=self._auto).pack(side="left")
        ttk.Button(bar, text="Cancel", command=self.win.destroy).pack(side="right")
        self.ok = ttk.Button(bar, text="Split into two photos", command=self._ok)
        self.ok.pack(side="right", padx=4)
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.win.bind("<Return>", lambda e: self._ok())
        self._start = None
        self._auto()
        self.win.grab_set()

    def _auto(self):
        self._set(split.find_cut(self.glass))

    def _set(self, line):
        self.line = line
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            self.boxes = split.boxes(self.glass, line, self.dpi)
        finally:
            self.win.config(cursor="")
        self._draw()

    def _draw(self):
        (x0, y0), (x1, y1) = self.line
        for n, (canvas, _) in enumerate(self.pics):
            canvas.delete("cut")
            w, h = int(canvas["width"]), int(canvas["height"])
            if n == 0:  # the front: what will be saved
                f = w / self.glass.shape[1]
                for r in self.boxes:
                    pts = (cv2.boxPoints(r) * f).ravel().tolist()
                    canvas.create_polygon(*pts, outline="#1db954", fill="", width=3, tags="cut")
            canvas.create_line(x0 * w, y0 * h, x1 * w, y1 * h, fill="red", width=3, dash=(8, 4), tags="cut")

    def _press(self, event):
        self._start = (event.x, event.y)

    def _drag(self, event, canvas):
        if self._start:
            canvas.delete("drawing")
            canvas.create_line(*self._start, event.x, event.y, fill="red", width=2, tags="drawing")

    def _release(self, event, canvas):
        if not self._start:
            return
        canvas.delete("drawing")
        w, h = int(canvas["width"]), int(canvas["height"])
        (sx, sy), self._start = self._start, None
        if abs(event.x - sx) + abs(event.y - sy) < 12 * self.app.ui:  # a click: look for the gap around there
            self._set(split.find_cut(self.glass, near=(min(max(event.x / w, 0.02), 0.98),
                                                       min(max(event.y / h, 0.02), 0.98))))
        else:
            line = split.line_through((sx / w, sy / h), (event.x / w, event.y / h))
            if line:
                self._set(line)

    def _ok(self):
        if self.app.busy:
            messagebox.showinfo("Split photo", "Wait for the current job to finish first.", parent=self.win)
            return
        self.win.destroy()
        self.app.start_split(self.row, self.glass, self.back, self.line)
