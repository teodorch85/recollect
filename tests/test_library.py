"""Library actions, run on a temporary library: splitting, turning, turning upright, duplicates."""
import numpy as np
import pyexiv2
import cv2

import duplicates
import fakes
import library
import organizer
import rotate
import split
from conftest import files

QUALITY = 90


def add(cat, pid, img, batch="Γάμος", dpi=300, back=None, **fields):
    inbox = library.inbox(cat)
    fakes.jpeg(inbox / f"{pid}.jpg", img)
    row = {"status": "ready", "batch": batch, "dpi": dpi, "path": f"{organizer.INBOX}/{pid}.jpg", "back_path": "",
           "back_status": "blank", "rotated": 0, "turn": 0}
    if back is not None:
        fakes.jpeg(inbox / f"{pid}_back.jpg", back)
        row.update(back_path=f"{organizer.INBOX}/{pid}_back.jpg", back_status="saved")
    cat.upsert(pid, **{**row, **fields})


def merged_pair():
    img = fakes.lid(1350, 2500)
    fakes.place(img, fakes.picture(1180, 1170, 1), 625, 675, 3)
    fakes.place(img, fakes.picture(1180, 1170, 2), 1850, 675, -2.5)
    return img


def back_with_writing_on_the_left():
    back = np.full((675, 1250, 3), 238, np.uint8)
    cv2.putText(back, "Maria 1994", (60, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (50, 50, 50), 5)
    return back


def stored(cat, pid):
    return duplicates.read_image(cat.root / cat.rows("id = ?", (pid,))[0]["path"], raw=True)


def test_split_front_and_back(cat, recycle_bin):
    add(cat, "00006", merged_pair(), back=back_with_writing_on_the_left())
    add(cat, "00007", fakes.picture(300, 450, 3))
    organizer.organize(cat)
    row = cat.rows("id = '00006'")[0]
    glass = stored(cat, "00006")
    back = duplicates.read_image(cat.root / row["back_path"], raw=True)
    message = library.split_photo(cat, row, glass, back, split.find_cut(glass), QUALITY,
                                  read_text=lambda img: "Μαρία 1994")
    assert message == "00006 split into 00006 and 00008. Back kept for 00006; the other was blank."
    rows = {r["id"]: r for r in cat.rows()}
    assert (rows["00006"]["back_status"], rows["00008"]["back_status"]) == ("saved", "blank")
    assert rows["00008"]["batch"] == "Γάμος" and rows["00008"]["dpi"] == "300"
    for pid in ("00006", "00008"):  # each print on its own, about 10 x 10 cm
        h, w = stored(cat, pid).shape[:2]
        assert abs(h - 1172) <= 4 and abs(w - 1162) <= 4
    # the year read from the back files the whole batch
    assert files(cat) == ["1994/Γάμος/1994_γάμος_00006.jpg", "1994/Γάμος/1994_γάμος_00007.jpg",
                          "1994/Γάμος/1994_γάμος_00008.jpg", "_backs/1994_γάμος_00006_back.jpg"]
    assert sorted(p.name for p in recycle_bin.iterdir()) == ["undated_γάμος_00006.jpg",
                                                             "undated_γάμος_00006_back.jpg"]


def test_split_keeps_the_auto_rotate_turn(cat):
    glass = merged_pair()
    add(cat, "00001", rotate.turn(glass, 90), rotated=90)  # Claude had turned the merged scan
    row = cat.rows()[0]
    library.split_photo(cat, row, rotate.turn(stored(cat, "00001"), 270), None, split.find_cut(glass), QUALITY)
    for pid in ("00001", "00002"):
        h, w = stored(cat, pid).shape[:2]
        assert h > 1100 and w > 1100  # square-ish prints, turned back the way they were
        assert cat.rows("id = ?", (pid,))[0]["rotated"] == "90"


def orientation(cat, pid):
    meta = pyexiv2.ImageData((cat.root / cat.rows("id = ?", (pid,))[0]["path"]).read_bytes())  # Greek folder
    try:
        return meta.read_exif().get("Exif.Image.Orientation")
    finally:
        meta.close()


def test_turn_photo_sets_only_the_orientation_tag(cat):
    add(cat, "00001", fakes.picture(300, 450))
    organizer.organize(cat)
    assert "00001 turned 90° clockwise" in library.turn_photo(cat, "00001", 90)
    assert orientation(cat, "00001") == "6" and stored(cat, "00001").shape[:2] == (300, 450)
    library.turn_photo(cat, "00001", 90)
    assert orientation(cat, "00001") == "3"
    library.turn_photo(cat, "00001", -180)
    assert orientation(cat, "00001") == "1"
    assert "no longer in the library" in library.turn_photo(cat, "99999", 90)


def test_turn_photo_refuses_tiff(cat):
    path = library.inbox(cat) / "00001.tif"
    library.save_image(path, fakes.picture(100, 150), 300, "TIFF", QUALITY)
    cat.upsert("00001", status="ready", batch="Γάμος", path=f"{organizer.INBOX}/00001.tif")
    assert "TIFF" in library.turn_photo(cat, "00001", 90)


def test_turn_upright(cat):
    add(cat, "00001", fakes.picture(300, 450, 1), turn=90)
    add(cat, "00002", fakes.picture(300, 450, 2))
    add(cat, "00003", fakes.picture(300, 450, 3), batch="Other")
    organizer.organize(cat)
    answers = [{"turn_clockwise": 90, "confidence": "high", "reason": "faces sideways"},
               {"turn_clockwise": 180, "confidence": "low", "reason": "no clear up"}]
    message = library.turn_upright(cat, "Γάμος", "key", QUALITY, orient_all=lambda key, photos: (answers, None))
    assert message == "“Γάμος”: 1 of 2 photo(s) turned upright."
    rows = {r["id"]: r for r in cat.rows()}
    assert stored(cat, "00001").shape[:2] == (450, 300)  # the pixels turned
    assert (rows["00001"]["rotated"], rows["00001"]["turn"]) == ("90", "0")  # and the hand turn reset
    assert stored(cat, "00002").shape[:2] == (300, 450)  # low confidence: left as it is
    assert rows["00002"]["orientation"] == "low: no clear up"


def test_find_duplicates(cat):
    photo = fakes.picture(400, 600, 1)
    add(cat, "00001", photo)
    add(cat, "00002", cv2.resize(photo, (540, 360)))  # the same print scanned again, smaller
    add(cat, "00003", fakes.picture(400, 600, 2))
    pairs, message = library.find_duplicates(cat)
    assert pairs == [("00002", "00001")]
    assert message == "Found 1 duplicate(s) among 3 photos."
    assert cat.rows("id = '00002'")[0]["duplicate_of"] == "00001"


def test_next_id_counts_files_waiting_in_the_inbox(cat):
    assert library.next_id(cat) == 1
    add(cat, "00004", fakes.picture(50, 50))
    fakes.jpeg(library.inbox(cat) / "00009_back.jpg", fakes.picture(50, 50))  # a file without a catalogue row
    assert library.next_id(cat) == 10


def test_file_photos(cat):
    add(cat, "00001", fakes.picture(50, 50), status="scanning")
    assert library.file_photos(cat, ["00001"]) == "Filed into Unknown year/Γάμος"
    assert cat.rows()[0]["status"] == "filed"
