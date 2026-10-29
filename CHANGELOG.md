# Changelog

Versions are MAJOR.MINOR.PATCH: MAJOR for changes that need your photo library migrated, MINOR for
new features, PATCH for fixes. The running version shows in the window title.

## 1.7.2 — 2026-10-29
- Fixed: Check now / Update now failed with an SSL certificate error. ReCollect.exe now checks
  GitHub's certificate with Windows' own certificate store, like the browser does.
  ReCollect 1.7.0 and 1.7.1 can't update themselves because of this: download 1.7.2 once from
  the releases page and unzip it over the old folder.

## 1.7.1 — 2026-10-29
- Reset scanner always switches the scanner back on in Windows, even if the reset fails half-way,
  and also revives a scanner Windows had left disabled.
- "Scanner not found" names the devices Windows does see, and suggests Reset scanner.

## 1.7.0 — 2026-10-29
- ReCollect.exe: download it from the GitHub releases page, unzip, and open it. No Python, git or
  command line needed. On first start it brings over your settings and calibration from the old
  version and points the Desktop and Start menu shortcuts at itself.
- Updates itself: every few hours it checks GitHub for a new release; a banner offers Update now,
  which downloads it, closes ReCollect and opens the new version (never mid-job or mid-bed).
  Settings ▸ Updates ▸ Check now checks straight away.
- ReCollect.exe keeps its settings and calibration in AppData\Local\ReCollect, so updates never
  touch them. Your photos stay where they are.

## 1.6.0 — 2026-10-29
- Reset scanner (Settings ▸ Scanning): when the light keeps flashing or scans fail, reconnects the
  scanner's USB device and restarts Windows' scanner service, like unplugging and plugging it in.
  Windows asks for permission. Scanner error messages point to it.

## 1.5.0 — 2026-10-29
- Settings window [Ctrl+,]: sections on the left (Library, Scanning, Image, Notifications,
  Updates, About), each setting with a short explanation; changes are saved at once.
- New settings: back resolution, notifications and taskbar flashing on/off, update checks on/off.
- Notifications page: send a test notification and open the Windows notification settings.
- Updates page: Check now. About page: Restore defaults.
- The main column is shorter: batch label, Scan backs, a one-line summary of the scan settings,
  and the Settings button. The photos folder can't be changed while prints wait for their backs.

## 1.4.0 — 2026-10-29
- A notification when a job finishes (the usual Windows popup in the corner, gone after about
  5 seconds) and a flashing taskbar button if ReCollect isn't in front, so you can wait away
  from the screen. Without Windows notifications, a small popup of ReCollect's own is shown.
- Cancel button [Esc]: stops a scan at the next safe point and keeps nothing from it. Cancelled
  fronts are deleted; cancelled backs leave the prints waiting for their backs.
- Progress bar for scans (an estimate: the V600 reports no progress) and for the duplicate check.
- Clearer Preview: says nothing was saved, one line per problem, what to do next, and that a
  15×20 print fits only one per scan.
- Update prompt: when a newer version lands in the folder, a banner offers Restart now, What's
  new or Later; restarting waits until no job or bed is in progress. Only one ReCollect runs at
  a time: starting it again brings the open window to the front.
- ReCollect.bat is gone: open ReCollect from its shortcut (Desktop, Start menu, or the new
  ReCollect shortcut in the app folder), which starts it with no console window.
- Once after updating: run `.venv\Scripts\pip install -r requirements.txt` (for the Windows
  notifications) and `.venv\Scripts\python install_shortcuts.py` (for the folder shortcut).

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
