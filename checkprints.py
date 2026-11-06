"""The Check prints window: the bed scan with the prints the program found, to confirm or fix.

Shown when the program is not sure how many prints are on the glass (two lying edge to edge are easily
taken for one), and on "Find prints again". The user can drag a line across a box to split it in two,
remove a box that is no print, undo, or look again with the finer settings; then Save or Cancel.
"""
import threading
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

import detect
import library
import split
import theme

SHOWN_PX = 1800                                   # the scan is shown from a copy this big, whatever its dpi
GOOD, UNSURE, PICKED = "#1db954", "#E8710A", theme.ACCENT   # box colours: fine, check me, selected
CUT = "#E81123"                                   # the line being dragged


class Boxes:
    """The prints as the user edits them: each rect with whether the program was unsure about it, and undo."""

    def __init__(self, rects, unsure=(), before=None):
        """before: the boxes as they were (the fronts saved now), for Undo to go back to."""
        self.items = [(r, i in set(unsure)) for i, r in enumerate(rects)]
        self._history = [[(r, False) for r in before]] if before is not None else []

    @property
    def rects(self):
        return [r for r, _ in self.items]

    @property
    def unsure(self):
        return [i for i, (_, doubt) in enumerate(self.items) if doubt]

    def _change(self, items):
        self._history.append(self.items)
        self.items = items

    def remove(self, i):
        self._change(self.items[:i] + self.items[i + 1:])

    def split(self, i, two):
        """Print i is really two prints (their rects): they take its place, sure."""
        self._change(self.items[:i] + [(r, False) for r in two] + self.items[i + 1:])

    def replace_all(self, rects, unsure=()):
        self._change([(r, i in set(unsure)) for i, r in enumerate(rects)])

    def undo(self):
        """Take back the last change; False when there is none."""
        if not self._history:
            return False
        self.items = self._history.pop()
        return True


def count_text(n):
    """The big question at the top."""
    if n == 0:
        return "No prints left: nothing to save"
    return f"{library.plural(n, 'print')} found. Is that right?"


class CheckPrints:
    """on_save(rects) is called with the confirmed prints, on_cancel() when the user gives up.
    find_again(img, dpi) -> (rects, unsure) looks again with the finer settings (it runs in a thread)."""

    def __init__(self, app, img, dpi, rects, unsure, on_save, on_cancel, find_again=None, finer_already=False,
                 before=None, note=None):
        self.app, self.img, self.dpi = app, img, dpi
        self.on_save, self.on_cancel, self.find_again = on_save, on_cancel, find_again
        self.boxes = Boxes(rects, unsure, before)
        f = min(1.0, SHOWN_PX / max(img.shape[:2]))
        self.small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        self.selected = None
        self.working = False
        self._start = None
        self._tk_img = None
        self.shown = (0, 0, 1.0)          # where the picture sits on the canvas: (x, y, canvas px per bed px)
        self.win = tk.Toplevel(app.root)
        self.win.title("Check prints")
        self.win.transient(app.root)
        ui = app.ui
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w, h = int(min(sw * 0.92, 1500 * ui)), int(sh * 0.86)
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
        self.win.minsize(int(700 * ui), int(500 * ui))
        self._build(finer_already)
        if note:
            self.note.set(note)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)
        for keys, fn in (("<Return>", self._save), ("<Escape>", self._cancel), ("<Delete>", self._remove),
                         ("<BackSpace>", self._remove), ("<Control-z>", self._undo), ("<Control-Z>", self._undo)):
            self.win.bind(keys, lambda e, fn=fn: fn())
        self.win.focus_set()
        self._refresh()
        self._grab()

    def _grab(self, tries=20):
        """The main window waits until this one is dealt with. A window not yet on screen can't take the grab
        (X11): try again shortly; the main window also refuses to act while this one is open (App._checking)."""
        if not self.win.winfo_exists():
            return
        try:
            self.win.grab_set()
        except tk.TclError:
            if tries:
                self.win.after(100, self._grab, tries - 1)

    # ---------- layout ----------
    def _build(self, finer_already):
        top = ttk.Frame(self.win, padding=(14, 10, 14, 0))
        top.pack(fill="x")
        self.count = tk.StringVar()
        ttk.Label(top, textvariable=self.count, style="Title.TLabel").pack(anchor="w")
        self.sub = tk.StringVar()
        sub = ttk.Label(top, textvariable=self.sub, style="Status.TLabel", justify="left")
        sub.pack(anchor="w", fill="x", pady=(2, 0))
        sub.bind("<Configure>", lambda e: sub.config(wraplength=max(e.width - 4, 200)))

        bar = ttk.Frame(self.win, padding=(14, 8, 14, 12))
        bar.pack(side="bottom", fill="x")
        self.note = tk.StringVar(value="Drag a line across a box to split it in two.  Click a box, then press "
                                       "Delete if it is no print.  Ctrl+Z undoes.")
        # plain 11 pt in the muted colour: Muted.TLabel's 10 pt is too small for the hints and errors shown here
        note = ttk.Label(self.win, textvariable=self.note, foreground=theme.MUTED, padding=(14, 4))
        note.pack(side="bottom", anchor="w")
        self.remove_btn = ttk.Button(bar, text="Not a print  [Delete]", command=self._remove)
        self.remove_btn.pack(side="left")
        self.undo_btn = ttk.Button(bar, text="Undo  [Ctrl+Z]", command=self._undo)
        self.undo_btn.pack(side="left", padx=6)
        self.again_btn = ttk.Button(bar, text="Find again (finer)", command=self._again)
        self.again_btn.pack(side="left")
        if self.find_again is None or finer_already:
            self.again_btn.pack_forget()
        ttk.Button(bar, text="Cancel  [Esc]", command=self._cancel).pack(side="right")
        self.save_btn = ttk.Button(bar, style="Accent.TButton", command=self._save)
        self.save_btn.pack(side="right", padx=6)

        self.canvas = tk.Canvas(self.win, background=theme.PREVIEW, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        self.canvas.bind("<Configure>", lambda e: self._fit())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    # ---------- drawing ----------
    def _fit(self):
        """Fit the scan inside the canvas (again whenever the window is resized) and draw the boxes."""
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 50 or ch < 50:
            return
        h, w = self.img.shape[:2]
        f = min(cw / w, ch / h)
        size = (max(1, round(w * f)), max(1, round(h * f)))
        pic = cv2.resize(self.small, size, interpolation=cv2.INTER_AREA)
        self._tk_img = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(pic, cv2.COLOR_BGR2RGB)))
        self.shown = ((cw - size[0]) / 2, (ch - size[1]) / 2, f)
        self.canvas.delete("all")
        self.canvas.create_image(self.shown[0], self.shown[1], anchor="nw", image=self._tk_img)
        self._draw()

    def _to_canvas(self, x, y):
        ox, oy, f = self.shown
        return ox + x * f, oy + y * f

    def _to_bed(self, x, y):
        ox, oy, f = self.shown
        return (x - ox) / f, (y - oy) / f

    def _draw(self):
        """The boxes: green, orange where the program is unsure, blue when selected; each with its number."""
        self.canvas.delete("box")
        doubts = set(self.boxes.unsure)
        for i, r in enumerate(self.boxes.rects):
            pts = [c for p in cv2.boxPoints(r) for c in self._to_canvas(*p)]
            colour = PICKED if i == self.selected else UNSURE if i in doubts else GOOD
            self.canvas.create_polygon(*pts, outline=colour, fill="", width=6 if i == self.selected else 4, tags="box")
            x, y = self._to_canvas(*r[0])
            text = self.canvas.create_text(x, y, text=str(i + 1), fill="white", font=(theme.FONT, 20, "bold"),
                                           tags="box")
            x0, y0, x1, y1 = self.canvas.bbox(text)
            tag = self.canvas.create_rectangle(x0 - 10, y0 - 4, x1 + 10, y1 + 4, fill=colour, outline="", tags="box")
            self.canvas.tag_lower(tag, text)

    def _refresh(self):
        """Texts and buttons for the boxes as they are now."""
        n = len(self.boxes.rects)
        doubts = self.boxes.unsure
        self.count.set(count_text(n))
        if not n:
            self.sub.set("Undo (Ctrl+Z) brings the boxes back; Cancel saves nothing.")
        elif doubts:
            names = detect.print_names(doubts)
            self.sub.set(f"Orange: I am not sure about {names}. Is it one print or two? "
                         "Split or remove boxes until there is one per print.")
        else:
            self.sub.set("One box per print? Then save. If not, fix it here before anything is saved.")
        self.save_btn.config(text=f"Save {library.plural(n, 'photo')}  [Enter]" if n else "Nothing to save",
                             state="normal" if n and not self.working else "disabled")
        idle = "disabled" if self.working else "normal"
        self.remove_btn.config(state="normal" if self.selected is not None and not self.working else "disabled")
        self.undo_btn.config(state="normal" if self.boxes._history and not self.working else "disabled")
        self.again_btn.config(state=idle)
        self._draw()

    # ---------- the mouse ----------
    def _press(self, event):
        if not self.working:
            self._start = (event.x, event.y)

    def _drag(self, event):
        if self._start:
            self.canvas.delete("drawing")
            self.canvas.create_line(*self._start, event.x, event.y, fill=CUT, width=4, dash=(10, 5), tags="drawing")

    def _release(self, event):
        if not self._start:
            return
        self.canvas.delete("drawing")
        (sx, sy), self._start = self._start, None
        if abs(event.x - sx) + abs(event.y - sy) < 12 * self.app.ui:  # a click: pick the box under it
            i = detect.hit(self.boxes.rects, *self._to_bed(event.x, event.y))
            self.selected = None if i == self.selected else i
            self._refresh()
            return
        p, q = self._to_bed(sx, sy), self._to_bed(event.x, event.y)
        i = detect.hit(self.boxes.rects, (p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if i is None:
            self.note.set("That line is not across a box. Drag from one side of the box to the other.")
            return
        self._split(i, p, q)

    # ---------- actions ----------
    def _work(self, message, job, done):
        """Run job() in a thread with a busy cursor; then done(result) here. Input waits meanwhile."""
        self.working = True
        self.note.set(message)
        self.win.config(cursor="watch")
        self.canvas.config(cursor="watch")
        self._refresh()
        out = {}

        def run():
            try:
                out["result"] = job()
            except Exception as e:  # report it in the window, never lose it
                out["error"] = f"{type(e).__name__}: {e}"
        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def poll():
            if not self.win.winfo_exists():  # closed meanwhile: the result is not wanted
                return
            if thread.is_alive():
                self.app.root.after(80, poll)
                return
            self.working = False
            self.win.config(cursor="")
            self.canvas.config(cursor="crosshair")
            if "error" in out:
                self.note.set("⚠ " + out["error"])
                self._refresh()
            else:
                done(out["result"])
        self.app.root.after(80, poll)

    def _split(self, i, p, q):
        rect = self.boxes.rects[i]
        self._work(f"Splitting print {i + 1}…", lambda: split.rect_in_two(self.img, rect, self.dpi, p, q),
                   lambda two: self._split_done(i, two))

    def _split_done(self, i, two):
        if not two or len(two) != 2:
            self.note.set("That cut would leave a piece too small for a print. Drag across the box where the "
                          "two prints meet, from one side of it to the other.")
        else:
            self.boxes.split(i, two)
            self.selected = None
            self.note.set(f"Print {i + 1} is now prints {i + 1} and {i + 2}. Wrong? Ctrl+Z.")
        self._refresh()

    def _remove(self):
        if self.working or self.selected is None:
            return
        self.boxes.remove(self.selected)
        self.note.set(f"Print {self.selected + 1} removed. Wrong? Ctrl+Z.")
        self.selected = None
        self._refresh()

    def _undo(self):
        if not self.working and self.boxes.undo():
            self.selected = None
            self.note.set("Undone.")
            self._refresh()

    def _again(self):
        if self.working or not self.find_again:
            return
        before = len(self.boxes.rects)
        self._work("Looking again, more carefully…", lambda: self.find_again(self.img, self.dpi),
                   lambda found: self._again_done(found, before))

    def _again_done(self, found, before):
        rects, unsure = found
        self.boxes.replace_all(rects, unsure)
        self.selected = None
        self.note.set(f"Looked again: {library.plural(len(rects), 'print')} (before: {before}). "
                      "Not better? Ctrl+Z.")
        self._refresh()

    def _save(self):
        if self.working or not self.boxes.rects:
            return
        rects = self.boxes.rects
        self._close()
        self.on_save(rects)

    def _cancel(self):
        self._close()
        self.on_cancel()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
