# Changelog

Versions are MAJOR.MINOR.PATCH: MAJOR for changes that need your photo library migrated, MINOR for
new features, PATCH for fixes. The running version shows in the window title.

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
