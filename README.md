# ReCollect

Scan, sort and keep your printed photos with an Epson Perfection V600 on Windows.

- Scans several prints at once, splits and straightens them, then scans their backs in the same spots.
- Reads handwriting and lab stamps on the backs and the date a camera printed on the front.
- Files the photos into Year / Batch folders with metadata that Immich and other photo apps read.
- Finds duplicates, even reprints and differently cropped scans.

## Install

1. Download `ReCollect-<version>.zip` from [Releases](https://github.com/teodorch85/recollect/releases/latest).
2. Unzip it into a folder of your own, e.g. Documents (not Program Files, so it can update itself).
3. Open `ReCollect.exe` in the unzipped folder. Windows may warn about an unknown app the first time:
   choose **More info ▸ Run anyway**.

ReCollect adds itself to the Start menu and Desktop, and offers new versions itself.

## From source

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python install_shortcuts.py
```

Changes are listed in [CHANGELOG.md](CHANGELOG.md).
