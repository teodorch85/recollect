"""A whole bed, scanned at 150 dpi: fronts cut out and saved, backs matched and read, Cancel, preview."""
from pathlib import Path

import cv2
import numpy as np
import pytest

import bed
import config
import fakes
import library
from conftest import files

DPI = 150  # the scanner's preview resolution: fast, and the prints are still big enough to find
H, W = int(11.7 * DPI), int(8.5 * DPI)  # the V600's glass
PRINTS = [((885, 590), 330, 520, 1), ((885, 590), 960, 520, -2), ((590, 885), 640, 1330, 0)]
SETTINGS = {**config.DEFAULTS, "sharpen": "Off", "restore_color": False, "jpeg_quality": 90}


@pytest.fixture
def glass():
    """(scan of three prints, scan of the empty bed)."""
    img = fakes.lid(H, W)
    for n, (hw, x, y, a) in enumerate(PRINTS):
        fakes.place(img, fakes.picture(*hw, seed=n), x, y, a)
    return img, fakes.lid(H, W, seed=9)


def backs(text_on=(0,)):
    """The same prints flipped over: white paper, writing on some."""
    img = fakes.lid(H, W)
    for n, (hw, x, y, a) in enumerate(PRINTS):
        paper = np.full((*hw, 3), 243, np.uint8)
        if n in text_on:
            cv2.putText(paper, "Crete 1994", (40, hw[0] // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (60, 60, 60), 3)
        fakes.place(img, paper, x, y, -a)  # a print flipped over mirrors its tilt
    return img


class CancelWhenSaving(library.Job):
    """Presses Cancel once the fronts start being saved."""
    def __init__(self):
        self.at = 0

    def progress(self, value):
        self.at = value

    def cancelled(self):
        return self.at >= 0.8


def test_fronts_then_backs(cat, glass):
    img, empty = glass
    done = bed.save_fronts(cat, img, DPI, empty, SETTINGS, "1994 Crete", scan_backs=True)
    assert done.message.startswith("Saved 3 fronts")
    assert [p["id"] for p in done.bed] == ["00001", "00002", "00003"]
    assert [r["status"] for r in cat.rows()] == ["scanning"] * 3
    rows = cat.rows()
    assert sorted(r["print_format"] for r in rows) == ["10x15"] * 3
    assert files(cat) == ["_inbox/00001.jpg", "_inbox/00002.jpg", "_inbox/00003.jpg"]

    view, message = bed.save_backs(cat, backs(text_on=(0,)), DPI, done.bed, empty, SETTINGS,
                                   read_text=lambda img: "Κρήτη 1994")
    assert message.startswith(f"Backs: 1 kept, 2 blank. Filed into {Path('1994/Crete')}")  # \\ on Windows
    status = {r["id"]: (r["status"], r["back_status"], r["back_text"]) for r in cat.rows()}
    assert status == {"00001": ("filed", "saved", "Κρήτη 1994"), "00002": ("filed", "blank", None),
                      "00003": ("filed", "blank", None)}
    assert files(cat) == ["1994/Crete/1994_crete_00001.jpg", "1994/Crete/1994_crete_00002.jpg",
                          "1994/Crete/1994_crete_00003.jpg", "_backs/1994_crete_00001_back.jpg"]


def test_without_backs_photos_are_filed_at_once(cat, glass):
    img, empty = glass
    done = bed.save_fronts(cat, img, DPI, empty, SETTINGS, "Crete", scan_backs=False)
    assert done.bed == [] and f"Filed into {Path('Unknown year/Crete')}" in done.message
    assert {r["back_status"] for r in cat.rows()} == {"not scanned"}


def test_auto_rotate_answer_turns_the_front(cat, glass):
    img, empty = glass
    answers = [{"turn_clockwise": 90, "confidence": "high", "reason": "sideways"}, None,
               {"turn_clockwise": 180, "confidence": "low", "reason": "no clear up"}]
    done = bed.save_fronts(cat, img, DPI, empty, SETTINGS, "Crete", True, orient=lambda photos: (answers, None))
    assert "1 turned upright" in done.message
    assert [r["rotated"] for r in cat.rows()] == ["90", "0", "0"]


def test_cancel_keeps_nothing(cat, glass):
    img, empty = glass
    with pytest.raises(library.Cancelled):
        bed.save_fronts(cat, img, DPI, empty, SETTINGS, "Crete", True, job=CancelWhenSaving())
    assert cat.rows() == [] and files(cat) == []


def test_cancelled_backs_keep_the_bed_waiting(cat, glass):
    img, empty = glass
    done = bed.save_fronts(cat, img, DPI, empty, SETTINGS, "Crete", True)

    class CancelAfterFirstBack(library.Job):
        n = 0

        def note(self, text):
            self.n += 1

        def cancelled(self):
            return self.n >= 1
    with pytest.raises(library.Cancelled):
        bed.save_backs(cat, backs(text_on=(0, 1)), DPI, done.bed, empty, SETTINGS, CancelAfterFirstBack(),
                       read_text=lambda img: "")
    assert [r["status"] for r in cat.rows()] == ["scanning"] * 3
    assert files(cat) == ["_inbox/00001.jpg", "_inbox/00002.jpg", "_inbox/00003.jpg"]


def test_prints_lying_back_side_up_are_not_saved(cat, glass):
    _, empty = glass
    done = bed.save_fronts(cat, backs(), DPI, empty, SETTINGS, "Crete", True)
    assert done.message.startswith("Nothing saved: 3 prints are lying back-side down")
    assert cat.rows() == []


def test_drop_forgets_a_bed(cat, glass):
    img, empty = glass
    done = bed.save_fronts(cat, img, DPI, empty, SETTINGS, "Crete", True)
    bed.drop(cat, done.bed)
    assert cat.rows() == [] and files(cat) == []


def test_preview(glass):
    img, empty = glass
    view, message = bed.check_placement(img, empty, DPI)
    assert "Found 3 prints." in message
    assert "Print 2 reaches the edge of the glass" in message and "Prints 1 and 2 are too close" in message
    well_placed = fakes.lid(H, W)
    fakes.place(well_placed, fakes.picture(590, 885), 640, 600)
    fakes.place(well_placed, fakes.picture(590, 885, 1), 640, 1300)
    _, message = bed.check_placement(well_placed, empty, DPI)
    assert "Found 2 prints." in message and "Placement looks good" in message
    _, message = bed.check_placement(fakes.lid(H, W), empty, DPI)
    assert "No prints found" in message


def test_back_region_covers_the_prints():
    region = bed.back_region([{"rect": ((300, 400), (200, 100), 0), "dpi": 100}], 300)
    assert region == pytest.approx((1.6, 3.1, 2.8, 1.8))


def test_scan_time_estimate():
    assert bed.scan_seconds(300) == 15 and bed.scan_seconds(600) == 65
    assert bed.scan_seconds(600, share=0.1) == 8  # never claims less than 8 s
