"""ReCollect's look: Windows 11 style controls (the Sun Valley theme) and the colours and fonts every
window shares, so the main window, Settings and Review look like one app."""
from tkinter import ttk

PAGE, CARD, SIDEBAR, BORDER = "#FAFAFA", "#FFFFFF", "#F3F3F3", "#E5E5E5"
TEXT, MUTED, ACCENT, HOVER = "#1A1A1A", "#666666", "#005FB8", "#EAEAEA"
ACCENT_SOFT, ACCENT_DARK = "#E6F0FA", "#003E78"   # info banner: tint and text
PREVIEW = "#2B2B2B"                               # behind the bed picture: photos read best on dark
FONT = "Segoe UI"


def apply(root):
    """Turn on the theme for the whole app (call once, right after tk.Tk())."""
    try:
        import sv_ttk
        sv_ttk.set_theme("light", root)
    except Exception:  # theme missing or broken: Windows' own look still works
        ttk.Style(root).theme_use("vista" if "vista" in ttk.Style(root).theme_names() else "clam")
    root.configure(background=PAGE)
    # The theme sizes its fonts in pixels, which don't grow on a scaled display (125%, 150%): points do
    for name, size in (("SunValleyCaptionFont", 10), ("SunValleyBodyFont", 11), ("SunValleyBodyStrongFont", 11),
                       ("SunValleyBodyLargeFont", 13)):
        try:
            root.tk.call("font", "configure", name, "-size", size)
        except Exception:  # not the Sun Valley theme
            pass
    s = ttk.Style(root)
    s.configure("TButton", font=(FONT, 11))
    s.configure("TLabel", font=(FONT, 11))
    s.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 10))
    s.configure("Heading.TLabel", font=(FONT, 12, "bold"))
    s.configure("Title.TLabel", font=(FONT, 16, "bold"))
    s.configure("Status.TLabel", font=(FONT, 12))
    s.configure("Big.Accent.TButton", font=(FONT, 14, "bold"), padding=(10, 10))
    s.configure("TCheckbutton", font=(FONT, 11))
    s.configure("Switch.TCheckbutton", font=(FONT, 11))
    root.option_add("*TCombobox*Listbox.font", (FONT, 11))  # the drop-down lists
