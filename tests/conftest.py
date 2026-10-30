"""Shared set-up: the app's modules on the path, stand-ins for Windows-only ones elsewhere, and a
fresh photo library in a temporary folder for each test."""
import shutil
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]

if sys.platform != "win32":  # Windows OCR and the scanner exist only on Windows: the logic is tested anyway
    sys.modules.setdefault("ocr", types.SimpleNamespace(read_text=lambda img: "", read_line=lambda img: ""))

import naming  # noqa: E402
import organizer  # noqa: E402


@pytest.fixture(autouse=True)
def recycle_bin(tmp_path, monkeypatch):
    """organizer.recycle sends files here instead of to the Windows Recycle Bin."""
    bin_ = tmp_path / "Recycle Bin"
    bin_.mkdir()
    monkeypatch.setattr(organizer, "recycle", lambda f: shutil.move(str(f), bin_ / Path(f).name))
    monkeypatch.setattr(organizer, "template", naming.parse(naming.DEFAULT))
    return bin_


@pytest.fixture
def cat(tmp_path):
    return organizer.Catalog(tmp_path / "PhotoScans")


def files(cat):
    """Every image in the library, as paths relative to it (forward slashes)."""
    return sorted(p.relative_to(cat.root).as_posix() for p in cat.root.rglob("*") if p.suffix in (".jpg", ".tif"))
