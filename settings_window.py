"""The Settings window: sections in a sidebar on the left, their settings in cards on the right.

Every change is saved at once (no Save button). The settings themselves live on the App as Tk
variables, so the main window and this one always show the same values.
"""
import os
import tkinter as tk
from tkinter import messagebox, ttk

import enhance
import notify
import organizer
from version import __version__

SIDEBAR, PAGE, CARD, BORDER = "#F3F3F3", "#FAFAFA", "#FFFFFF", "#E5E5E5"
TEXT, MUTED, ACCENT, HOVER = "#1A1A1A", "#666666", "#0F6E56", "#E8E8E8"
SECTIONS = ("Library", "Scanning", "Image", "Notifications", "Updates", "About")


class Switch(tk.Canvas):
    """An on/off switch bound to a BooleanVar."""

    def __init__(self, parent, variable, ui):
        self.w, self.h = int(40 * ui), int(22 * ui)
        super().__init__(parent, width=self.w, height=self.h, background=CARD, highlightthickness=0,
                         cursor="hand2")
        self.var = variable
        self.bind("<Button-1>", lambda e: self.var.set(not self.var.get()))
        trace = variable.trace_add("write", lambda *a: self._draw())
        self.bind("<Destroy>", lambda e: variable.trace_remove("write", trace))
        self._draw()

    def _draw(self):
        self.delete("all")
        on, w, h = bool(self.var.get()), self.w, self.h
        fill = ACCENT if on else CARD
        line = ACCENT if on else "#8A8A8A"
        self.create_oval(1, 1, h - 1, h - 1, fill=fill, outline=line)
        self.create_oval(w - h + 1, 1, w - 1, h - 1, fill=fill, outline=line)
        self.create_rectangle(h / 2, 1, w - h / 2, h - 1, fill=fill, outline="")
        self.create_line(h / 2, 1, w - h / 2, 1, fill=line)
        self.create_line(h / 2, h - 1, w - h / 2, h - 1, fill=line)
        k = h * 0.28  # knob radius
        cx = w - h / 2 if on else h / 2
        self.create_oval(cx - k, h / 2 - k, cx + k, h / 2 + k, fill="white" if on else "#5A5A5A", outline="")


class SettingsWindow:
    def __init__(self, app):
        self.app, ui = app, app.ui
        self.ui = ui
        self.win = tk.Toplevel(app.root, background=PAGE)
        self.win.title("ReCollect settings")
        self.win.transient(app.root)
        self.win.geometry(f"{int(860 * ui)}x{int(620 * ui)}")
        self.win.minsize(int(700 * ui), int(480 * ui))
        self.win.bind("<Escape>", lambda e: self.win.destroy())

        nav = tk.Frame(self.win, background=SIDEBAR, width=int(200 * ui), padx=8, pady=14)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(nav, text="Settings", font=("Segoe UI", 15, "bold"), background=SIDEBAR,
                 foreground=TEXT, anchor="w", padx=10).pack(fill="x", pady=(0, 12))
        self.items = {}
        for name in SECTIONS:
            item = tk.Frame(nav, background=SIDEBAR, cursor="hand2")
            item.pack(fill="x", pady=1)
            bar = tk.Frame(item, width=3, background=SIDEBAR)
            bar.pack(side="left", fill="y", pady=6)
            label = tk.Label(item, text=name, font=("Segoe UI", 11), background=SIDEBAR, foreground=TEXT,
                             anchor="w", padx=10, pady=7)
            label.pack(side="left", fill="x", expand=True)
            for w in (item, label):
                w.bind("<Button-1>", lambda e, n=name: self.show(n))
                w.bind("<Enter>", lambda e, n=name: self._hover(n, True))
                w.bind("<Leave>", lambda e, n=name: self._hover(n, False))
            self.items[name] = (item, bar, label)
        tk.Label(nav, text="Changes are saved\nautomatically.", font=("Segoe UI", 9), background=SIDEBAR,
                 foreground=MUTED, justify="left", anchor="w", padx=10).pack(side="bottom", fill="x")

        self.body = tk.Frame(self.win, background=PAGE, padx=int(28 * ui), pady=int(20 * ui))
        self.body.pack(side="left", fill="both", expand=True)
        self.current = None
        self.show("Library")

    # ---------- navigation ----------
    def show(self, name):
        self.current = name
        for n, (item, bar, label) in self.items.items():
            bg = CARD if n == name else SIDEBAR
            for w in (item, label):
                w.config(background=bg)
            bar.config(background=ACCENT if n == name else bg)
            label.config(foreground=ACCENT if n == name else TEXT,
                         font=("Segoe UI", 11, "bold") if n == name else ("Segoe UI", 11))
        for w in self.body.winfo_children():
            w.destroy()
        tk.Label(self.body, text=name, font=("Segoe UI", 18, "bold"), background=PAGE, foreground=TEXT,
                 anchor="w").pack(fill="x", pady=(0, 14))
        getattr(self, "_page_" + name.lower())()

    def _hover(self, name, inside):
        if name != self.current:
            item, bar, label = self.items[name]
            for w in (item, bar, label):
                w.config(background=HOVER if inside else SIDEBAR)

    # ---------- building blocks ----------
    def _card(self, heading=None):
        if heading:
            tk.Label(self.body, text=heading, font=("Segoe UI", 10, "bold"), background=PAGE, foreground=MUTED,
                     anchor="w").pack(fill="x", pady=(4, 4))
        card = tk.Frame(self.body, background=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 14))
        return card

    def _row(self, card, title, desc=None, control=None):
        """One setting: title and grey description on the left, its control on the right."""
        if card.winfo_children():
            tk.Frame(card, background=BORDER, height=1).pack(fill="x", padx=14)
        row = tk.Frame(card, background=CARD, padx=14, pady=10)
        row.pack(fill="x")
        if control:
            control(row).pack(side="right", padx=(16, 0))
        text = tk.Frame(row, background=CARD)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, font=("Segoe UI", 10, "bold"), background=CARD, foreground=TEXT,
                 anchor="w").pack(fill="x")
        if desc:
            kind = "textvariable" if isinstance(desc, tk.Variable) else "text"
            tk.Label(text, **{kind: desc}, font=("Segoe UI", 9), background=CARD, foreground=MUTED, anchor="w",
                     justify="left", wraplength=int(400 * self.ui)).pack(fill="x")
        return row

    def _switch(self, var):
        return lambda parent: Switch(parent, var, self.ui)

    @staticmethod
    def _choice(var, values, width=8):
        return lambda parent: ttk.Combobox(parent, textvariable=var, values=values, width=width, state="readonly")

    @staticmethod
    def _button(text, command):
        return lambda parent: ttk.Button(parent, text=text, command=command)

    def _buttons(self, *pairs):
        def make(parent):
            box = tk.Frame(parent, background=CARD)
            for i, (text, command) in enumerate(pairs):
                ttk.Button(box, text=text, command=command).pack(side="left", padx=(4 if i else 0, 0))
            return box
        return make

    def _note(self, var):
        tk.Label(self.body, textvariable=var, font=("Segoe UI", 9), background=PAGE, foreground=MUTED,
                 anchor="w", justify="left", wraplength=int(560 * self.ui)).pack(fill="x")

    # ---------- pages ----------
    def _page_library(self):
        a = self.app
        card = self._card()
        self._row(card, "Photos folder", a.out_var, self._buttons(("Change…", a._browse), ("Open", a._open_folder)))
        self._row(card, "Catalogue", "Every photo with its year, batch, back text and duplicates, as a spreadsheet.",
                  self._button("Open in Excel", a._open_catalog))
        card = self._card("How photos are filed")
        self._row(card, "Folders", f"Year / Batch, e.g. 1994 / Crete. Photos whose year isn't known go in "
                                   f"“{organizer.UNKNOWN}” until you set the year for their batch (main window).")

    def _page_scanning(self):
        a = self.app
        card = self._card("Fronts")
        self._row(card, "Resolution", "600 dpi suits most prints (about a minute per bed). 1200 is for small "
                                      "prints you want to enlarge, and is much slower.",
                  self._choice(a.dpi_var, [300, 600, 800, 1200]))
        card = self._card("Backs")
        self._row(card, "Scan backs", "After the fronts, turn the prints over and scan their backs for "
                                      "handwriting and lab stamps.", self._switch(a.backs_var))
        self._row(card, "Resolution", "300 dpi is enough to read handwriting; 600 helps with tiny lab print.",
                  self._choice(a.back_dpi_var, [300, 600]))
        card = self._card("Scanner")
        self._row(card, "Calibrate empty bed", "Scans the empty glass so prints can be told apart from the lid. "
                                               "Do it again if the lid or glass has changed.",
                  self._button("Calibrate…", self._calibrate))

    def _page_image(self):
        a = self.app
        card = self._card("Enhancement")
        self._row(card, "Unsharp mask", "Sharpens the scan like Epson Scan 2.",
                  self._choice(a.sharpen_var, list(enhance.SHARPEN_LEVELS)))
        self._row(card, "Color restoration", "Brings back colours of faded prints.", self._switch(a.restore_var))
        card = self._card("File")
        self._row(card, "Format", "JPEG for everyday use; TIFF keeps every detail but is several times larger.",
                  self._choice(a.fmt_var, ["JPEG", "TIFF"]))
        self._row(card, "JPEG quality", "100 is the most faithful (about 20 MB for a large print); 95 looks the "
                                        "same at about a third of the size.",
                  self._choice(a.quality_var, [90, 95, 100]))

    def _page_notifications(self):
        a = self.app
        card = self._card()
        self._row(card, "Notify when a job finishes", "A popup in the corner of the screen for about 5 seconds.",
                  self._switch(a.notify_var))
        self._row(card, "Flash the taskbar button", "When ReCollect isn't the window in front.",
                  self._switch(a.flash_var))
        self.test_result = tk.StringVar()
        card = self._card("Check")
        self._row(card, "Send a test notification", "See what a notification looks like on this PC.",
                  self._button("Send", self._test_notification))
        self._row(card, "Windows notification settings", "Turn on notifications for ReCollect and check Do not "
                                                         "disturb.", self._button("Open", self._windows_settings))
        self._note(self.test_result)

    def _page_updates(self):
        a = self.app
        card = self._card()
        self._row(card, "Check for updates", "Look for a newer version in the ReCollect folder every minute and "
                                             "offer to restart.", self._switch(a.updates_var))
        self.update_result = tk.StringVar()
        self._row(card, f"Version {__version__}", "The version running now.",
                  self._buttons(("Check now", self._check_now), ("What's new", a._whats_new)))
        self._note(self.update_result)

    def _page_about(self):
        a = self.app
        card = self._card()
        self._row(card, "ReCollect", f"Version {__version__}. Scan, sort and keep your printed photos.")
        self._row(card, "App folder", str(a.app_dir), self._button("Open", lambda: os.startfile(a.app_dir)))
        card = self._card("Reset")
        self._row(card, "Restore defaults", "Scanning, image, notification and update settings go back to how "
                                            "they came. Your photos folder and batch label stay.",
                  self._button("Restore…", self._restore))

    # ---------- actions ----------
    def _calibrate(self):
        if self.app.busy:
            messagebox.showinfo("Calibrate", "Wait for the current job to finish first.", parent=self.win)
            return
        self.win.destroy()
        self.app._ask_calibrate()

    def _test_notification(self):
        try:
            notify.job_done(self.app.root, "ReCollect test", "If you can read this, notifications work.",
                            flash_taskbar=False)
        except Exception as e:
            self.test_result.set(f"Couldn't show a notification: {e}")
            return
        if notify.last_error:
            self.test_result.set("Windows notifications aren't available, so ReCollect showed its own popup "
                                 f"instead.\n({notify.last_error})")
        else:
            self.test_result.set("Sent. If nothing appeared in the corner, Windows is hiding it: open the Windows "
                                 "notification settings, turn on ReCollect, and turn off Do not disturb.")

    @staticmethod
    def _windows_settings():
        os.startfile("ms-settings:notifications")

    def _check_now(self):
        new = self.app.check_update_now()
        self.update_result.set(f"ReCollect {new} is ready: use Restart now in the main window." if new else
                               "You're up to date.")

    def _restore(self):
        if messagebox.askyesno("Restore defaults", "Put scanning, image, notification and update settings back "
                                                   "to how they came?", parent=self.win):
            self.app.restore_defaults()
            self.show(self.current)
