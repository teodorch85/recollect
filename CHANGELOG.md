# Changelog

Versions are MAJOR.MINOR.PATCH: MAJOR for changes that need your photo library migrated, MINOR for
new features, PATCH for fixes. The running version shows in the window title.

## 1.3.1 — 2026-10-29
- Prints lying back-side down are recognised (white paper with almost no picture): Preview says
  which ones to turn over, and a front scan refuses to save them instead of filing blank paper.
- White prints are found by their paper outline (brighter than the lid), so Preview counts them
  correctly instead of boxing watermark text.
- Shorter placement warnings, one line per kind of problem.

## 1.3.0 — 2026-10-29
- Preview button [P]: a quick 150 dpi scan (about 14 s) that counts the prints on the glass and
  warns, in red, about prints closer than 3 mm, overlapping, or at the edge of the glass.
- Duplicate finder: every front gets a fingerprint; a print scanned before (even turned, cropped
  differently, or a smaller, faded reprint) is marked red as "new = original" right after the
  front scan, with a "Remove duplicates from this bed" button.
- Find duplicates… (Organize): checks the whole library, tags duplicates, and shows each pair side
  by side to delete one (to the Recycle Bin) or keep both (never asked again).

## 1.2.0 — 2026-10-29
- New name and icon: ReCollect. Desktop and Start menu shortcuts (`install_shortcuts.py`), own
  taskbar button.
- Settings and buttons in a column on the left, bed preview on the right.
- Version number in the window title, this changelog, and git history.

## 1.1.0 — 2026-10-27
- Photos carry their info in metadata Immich reads: back text as description, tags such as
  `Batch/…`, `Paper/…`, `Dated by/…` or `Undated`, and the date taken when the year is known.
- Reads the orange date a camera printed on the front (seven-segment decoder, OCR fallback).
- Year rules: set by you, back, batch label, same batch's backs, camera date, same batch's camera dates.
- Never clears a description written by another app; Greek kept out of the ASCII EXIF field.
- Plain-language scanner errors (e.g. scanner switched off mid-scan).

## 1.0.0 — 2026-10-26
- Scans fronts of several prints at once on the Epson V600 (WIA), splits and straightens them,
  then scans the backs in the same spots with a fast partial scan.
- Unsharp mask and colour restoration like Epson Scan 2; JPEG 100 or TIFF.
- Reads back text with Windows OCR (Greek and English); skips blank backs.
- Files photos into `Year/Batch/` by simple rules, with back text in the file name and everything
  in `photos.db` and `catalog.csv`.
