"""The Settings window: sections in a sidebar on the left, their settings in cards on the right.

Every change is saved at once (no Save button). The settings themselves live on the App as Tk
variables, so the main window and this one always show the same values.
"""
import os
import tkinter as tk
from tkinter import messagebox, ttk

import enhance
import notify
import naming
import organizer
import rotate
import updater
from version import __version__

SIDEBAR, PAGE, CARD, BORDER = "#F3F3F3", "#FAFAFA", "#FFFFFF", "#E5E5E5"
TEXT, MUTED, ACCENT, HOVER = "#1A1A1A", "#666666", "#0F6E56", "#E8E8E8"
SECTIONS = ("Library", "Scanning", "Image", "Claude", "Notifications", "Updates", "About")


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
        ttk.Button(nav, text="Close  [Esc]", command=self.win.destroy).pack(side="bottom", fill="x", padx=10)
        tk.Label(nav, text="Changes are saved\nautomatically.", font=("Segoe UI", 9), background=SIDEBAR,
                 foreground=MUTED, justify="left", anchor="w", padx=10).pack(side="bottom", fill="x", pady=(0, 8))

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

    def _row(self, card, title, desc=None, control=None, below=False):
        """One setting: title and grey description on the left, its control on the right (or under
        them, for wide controls)."""
        if card.winfo_children():
            tk.Frame(card, background=BORDER, height=1).pack(fill="x", padx=14)
        row = tk.Frame(card, background=CARD, padx=14, pady=10)
        row.pack(fill="x")
        if control and below:
            control(row).pack(side="bottom", anchor="w", pady=(8, 0))
        elif control:
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
        card = self._card("Naming template")
        self.tpl_var = tk.StringVar(value=a.s["name_template"])
        self.tpl_note = tk.StringVar()
        self._row(card, "Folders and file names", "Like Immich's storage template: {{y}} {{MM}} {{MMMM}} {{dd}} "
                  "{{album}} (the batch) {{id}} {{filename}} {{date}}, and {{#if y}}…{{else}}…{{/if}}. "
                  "'/' makes a folder.", self._template_box, below=True)
        tk.Label(card, textvariable=self.tpl_note, font=("Segoe UI", 9), background=CARD, foreground=ACCENT,
                 anchor="w", justify="left", wraplength=int(560 * self.ui), padx=14).pack(fill="x", pady=(0, 10))
        self.tpl_var.trace_add("write", lambda *e: self._preview_template())
        self._preview_template()

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
        self._row(card, "Reset scanner", "If the light keeps flashing or scans fail: restarts the scanner's "
                                         "connection and Windows' scanner service. Windows asks for permission.",
                  self._button("Reset…", self._reset_scanner))

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

    def _template_box(self, parent):
        box = tk.Frame(parent, background=CARD)
        presets = ttk.Combobox(box, values=naming.PRESETS, state="readonly", width=70)
        presets.set("Presets…")
        presets.bind("<<ComboboxSelected>>", lambda e: self.tpl_var.set(presets.get()))
        presets.pack(fill="x")
        ttk.Entry(box, textvariable=self.tpl_var, width=72).pack(fill="x", pady=(4, 0))
        row = tk.Frame(box, background=CARD)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Apply to library…", command=self._apply_template).pack(side="left")
        ttk.Button(row, text="Default", command=lambda: self.tpl_var.set(naming.DEFAULT)).pack(side="left", padx=4)
        return box

    def _preview_template(self):
        shown, problem = naming.example(self.tpl_var.get())
        saved = "" if self.tpl_var.get() == self.app.s["name_template"] else "  (not applied yet)"
        self.tpl_note.set(f"⚠ {problem}" if problem else f"e.g. {shown}{saved}")

    def _apply_template(self):
        template = self.tpl_var.get().strip()
        shown, problem = naming.example(template)
        if problem:
            messagebox.showinfo("Naming template", problem, parent=self.win)
            return
        if self.app.busy:
            messagebox.showinfo("Naming template", "Wait for the current job to finish first.", parent=self.win)
            return
        if messagebox.askokcancel("Naming template", "Every photo in your library will be renamed and moved to "
                                  f"match:\n\n{template}\n\ne.g. {shown}\n\nPhotos open in another program are "
                                  "moved next time.", parent=self.win):
            self.app.apply_template(template)
            self._preview_template()

    def _key_box(self, parent):
        box = tk.Frame(parent, background=CARD)
        self.key_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.key_var, show="•", width=44).pack(side="left")
        ttk.Button(box, text="Save", command=self._save_key).pack(side="left", padx=(4, 0))
        ttk.Button(box, text="Test", command=self._test_key).pack(side="left", padx=(4, 0))
        return box

    def _show_key_state(self, note=""):
        try:
            saved = bool(rotate.get_key())
        except Exception:
            saved = False
        self.key_state.set(note or ("Saved in Windows Credential Manager. Paste a new one to replace it, or save "
                                    "an empty box to remove it." if saved else
                                    "Needed for everything on this page. Create one at console.anthropic.com ▸ "
                                    "API keys (billing must be set up there), paste it here and press Save."))

    def _save_key(self):
        key = self.key_var.get().strip()
        if key and not key.startswith("sk-ant-"):
            self._show_key_state("That doesn't look like an Anthropic API key (they start with sk-ant-).")
            return
        rotate.set_key(key)
        self.key_var.set("")
        self._show_key_state("Key saved. Press Test to check it." if key else "Key removed.")

    def _test_key(self):
        key = self.key_var.get().strip() or rotate.get_key()
        if not key:
            self._show_key_state("Paste a key first.")
            return
        self._show_key_state("Testing…")
        self.win.update_idletasks()
        problem = rotate.test_key(key)
        self._show_key_state(problem or ("The key works." + ("" if self.key_var.get().strip() == "" else
                                                             " Press Save to keep it.")))

    def _page_claude(self):
        a = self.app
        card = self._card()
        self.key_state = tk.StringVar()
        self._row(card, "Anthropic API key", self.key_state, self._key_box, below=True)
        self._show_key_state()
        card = self._card("Uses")
        self._row(card, "Turn photos upright", "Claude looks at each new scan and turns sideways or upside-down "
                                               "photos the right way up. About 1 cent a photo.",
                  self._switch(a.rotate_var))
        self._row(card, "Suggest batch names", "Rename batch… ▸ Suggest with Claude, in the main window. About "
                                               "2 cents a suggestion.")

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
        where = ("Look for a new version on GitHub every few hours and offer to install it."
                 if updater.FROZEN else "Look for a newer version in the ReCollect folder every minute and "
                                        "offer to restart.")
        self._row(card, "Check for updates", where, self._switch(a.updates_var))
        self.update_result = tk.StringVar()
        self._row(card, f"Version {__version__}", "The version running now.",
                  self._buttons(("Check now", self._check_now), ("What's new", a._whats_new)))
        self._note(self.update_result)

    def _page_about(self):
        a = self.app
        card = self._card()
        self._row(card, "ReCollect", f"Version {__version__}. Scan, sort and keep your printed photos.")
        folder = updater.INSTALL_DIR or a.app_dir
        self._row(card, "App folder", str(folder), self._button("Open", lambda: os.startfile(folder)))
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

    def _reset_scanner(self):
        if self.app.busy:
            messagebox.showinfo("Reset scanner", "Wait for the current job to finish (or cancel it) first.",
                                parent=self.win)
            return
        if messagebox.askokcancel("Reset scanner", "ReCollect will reconnect the scanner and restart Windows' "
                                  "scanner service. It takes about 10 seconds; Windows will ask for permission.\n\n"
                                  "Prints on the glass can stay where they are.", parent=self.win):
            self.win.destroy()
            self.app.reset_scanner()

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
        self.update_result.set("Checking…")
        self.win.update_idletasks()
        new, problem = self.app.check_update_now()
        button = "Update now" if updater.FROZEN else "Restart now"
        self.update_result.set(problem or (f"ReCollect {new} is available: press {button} in the main window."
                                           if new else "You're up to date."))

    def _restore(self):
        if messagebox.askyesno("Restore defaults", "Put scanning, image, notification and update settings back "
                                                   "to how they came?", parent=self.win):
            self.app.restore_defaults()
            self.show(self.current)
