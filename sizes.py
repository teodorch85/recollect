"""Standard photo print sizes, and which one a scanned print is (used by the detector and the catalogue)."""

# Common print sizes in cm; the format hints at the era (e.g. 9x13 in the 70s-80s, 10x15 from the 90s).
PRINT_FORMATS = [(9, 9), (9, 13), (10, 10), (10, 15), (11, 15), (13, 13), (13, 18), (15, 20), (15, 21),
                 (18, 24), (20, 25), (20, 30), (6, 9), (7, 10), (8, 11)]


def print_size(w_px, h_px, dpi):
    """('20.3x15.0', '15x20') — measured size in cm and the nearest standard print format."""
    a, b = sorted((w_px / dpi * 2.54, h_px / dpi * 2.54))
    best = min(PRINT_FORMATS, key=lambda f: abs(f[0] - a) + abs(f[1] - b))
    fmt = f"{best[0]}x{best[1]}" if abs(best[0] - a) < 1.0 and abs(best[1] - b) < 1.0 else ""
    return f"{max(a, b):.1f}x{min(a, b):.1f}", fmt
