# Changelog

Versions are MAJOR.MINOR.PATCH: MAJOR for changes that need your photo library migrated, MINOR for
new features, PATCH for fixes. The running version shows in the window title.

## 1.14.0 — 2026-10-30
- Split photo… (Organize): when two prints lay too close and were saved as one photo, give its
  number (or file name) and ReCollect shows where it would cut, with the back if one is stored.
  Click to move the cut, or cut the other way. Front and back are cut along the same line, each
  back is read again, and the second print gets the next free number. The old files go to the
  Recycle Bin.

## 1.13.1 — 2026-10-30
Fixes from a review of 1.13.0:
- Left-click now turns a photo clockwise, as described (it turned it the other way).
- A rotation made in another app (Windows Photos, digiKam) is no longer reset when ReCollect
  rewrites a photo's info; ReCollect only sets the orientation of photos you turned in it.
- Clicking a photo rewrites just that photo, not the whole library, so the window stays quick.
- After changing the photos folder, the old preview no longer turns photos (it could turn a
  different photo with the same number in the new folder).
- The preview always shows the turn stored for each photo, also after Turn upright…
- Turn upright… looks at the photo as stored, so a turn set by hand isn't applied twice.
- TIFF photos can't take an orientation tag here: clicking one now says so instead of
  claiming it was turned. A problem writing the tag (e.g. photo open in a viewer) is shown.
- The preview keeps a small copy of the bed instead of the full scan, so memory is freed.

## 1.13.0 — 2026-10-29
- Turn photos in the preview: after a front scan, a green arrow in each outline shows where that
  photo's top is. Click a photo to turn it a quarter turn clockwise, right-click for the other
  way. It's a virtual turn: only the photo's orientation tag (EXIF) changes, which Immich,
  Windows and other photo apps follow; the scan itself is not re-saved. Turn upright… (Claude)
  turns the pixels instead and clears a turn set by hand.

## 1.12.1 — 2026-10-29
- Fixed: a " - " in a naming template came out as a single space ("01 Γάμος" instead of
  "01 - Γάμος").
- New preset: Year / MM - Batch / number, e.g. 2007/01 - Γάμος Βασίλη/00004.jpg.

## 1.12.0 — 2026-10-29
- Naming template (Settings ▸ Library), in the style of Immich's storage template: {{y}} {{yy}}
  {{M}} {{MM}} {{MMM}} {{MMMM}} {{d}} {{dd}}, {{album}} (the batch), {{id}}, {{filename}},
  {{date}}, {{paper}}, {{size}}, '/' for folders and {{#if …}}…{{else}}…{{/if}}. Presets, a live
  example, and Apply to library…, which renames and moves every photo to match. The default is
  the layout used so far (Year / Batch / date_batch_number). The photo number is always kept in
  the name.

## 1.11.1 — 2026-10-29
- Photos are refiled when ReCollect starts, so the shorter names apply at once.

## 1.11.0 — 2026-10-29
- Check for updates button next to Settings in the main window.
- Settings has a Close button [Esc], and a Claude page for the API key, auto-rotate and batch
  name suggestions (the Image page no longer runs off the window).
- Turn upright… (Organize): Claude checks every photo of an existing batch and turns sideways
  or upside-down ones the right way up (about 1 cent a photo).
- Shorter file names: no more back text at the end (OCR of lab stamps gave names like
  …_e-ndura). The back text stays in the photo's description.

## 1.10.1 — 2026-10-29
- Fixed: setting a year or renaming a batch could leave its photos in "Unknown year". Batch names
  now match however their spaces or Greek accents were stored; a photo moved by hand is found
  again by its number instead of being skipped.
- After Set year or Rename, the status line says why any photo of the batch didn't move (waiting
  for its backs, file not found, open in another program).

## 1.10.0 — 2026-10-29
- Rename batch… (Organize): pick a batch, type its new name; folders, file names and the Batch
  tag follow. Using another batch's name merges the two. If it is the label you're scanning
  with, that changes too.
- Suggest with Claude: looks at up to 6 of the batch's photos and the writing on their backs and
  proposes a short name in the same language (uses the auto-rotate API key; about 2 cents).

## 1.9.0 — 2026-10-29
- Month for a batch: Organize ▸ Set year and month for batch. The month goes into the photos'
  date taken (Immich sorts them by it) and their file names (1994-07_…); folders stay Year / Batch.
  Choosing a batch shows the year and month already set for it.
- Fixed: setting a year sometimes left photos where they were, without a word. Photos left
  half-finished when ReCollect closed while their bed waited for its backs are now filed (without
  backs) on the next start, and a photo that can't be moved, e.g. because it's open in a viewer,
  is named in the status line instead of being skipped silently. A photo whose info can't be
  written is still moved.

## 1.8.0 — 2026-10-29
- Auto-rotate (Settings ▸ Image): Claude looks at each new front scan and turns sideways or
  upside-down photos the right way up before they are saved, so the camera-date reader and the
  duplicate finder see them upright too. About 1 cent a photo, with your own Anthropic API key
  (kept in Windows Credential Manager, never in a file). Photos with no clear "up" stay as they
  are and get the tag "Check orientation". Off until you turn it on.

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
