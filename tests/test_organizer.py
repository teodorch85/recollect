import shutil
import unicodedata

import numpy as np
import pyexiv2

import fakes
import organizer
from conftest import files


def add(cat, pid, batch="Γάμος Βασίλη", status="ready", **fields):
    path = cat.root / organizer.INBOX / f"{pid}.jpg"
    fakes.jpeg(path, np.full((60, 90, 3), 128, np.uint8))
    cat.upsert(pid, **{"status": status, "batch": batch, "path": f"{organizer.INBOX}/{pid}.jpg", "back_path": "",
                       "back_status": "not scanned", **fields})


def test_files_photos_by_year_and_batch(cat):
    add(cat, "00001")
    add(cat, "00002", back_text="Κρήτη 1994")
    organizer.organize(cat)
    assert files(cat) == ["1994/Γάμος Βασίλη/1994_γάμος-βασίλη_00001.jpg",  # year from the other print's back
                          "1994/Γάμος Βασίλη/1994_γάμος-βασίλη_00002.jpg"]
    assert [r["year"] for r in cat.rows()] == ["1994", "1994"]


def test_unknown_year(cat):
    add(cat, "00001")
    organizer.organize(cat)
    assert files(cat) == ["Unknown year/Γάμος Βασίλη/undated_γάμος-βασίλη_00001.jpg"]


def test_year_and_month_set_by_hand(cat):
    add(cat, "00001")
    organizer.set_batch_year(cat, "Γάμος Βασίλη", "2007", 1)
    assert files(cat) == ["2007/Γάμος Βασίλη/2007-01_γάμος-βασίλη_00001.jpg"]
    assert organizer.batch_date(cat, "Γάμος Βασίλη") == ("2007", "01")
    meta = pyexiv2.ImageData((cat.root / cat.rows()[0]["path"]).read_bytes())  # Greek folder: as metadata.py
    assert meta.read_exif()["Exif.Photo.DateTimeOriginal"].startswith("2007:01:")
    meta.close()
    organizer.set_batch_year(cat, "Γάμος Βασίλη", "")  # back to automatic
    assert files(cat) == ["Unknown year/Γάμος Βασίλη/undated_γάμος-βασίλη_00001.jpg"]


def test_batch_names_match_whatever_the_unicode_form(cat):  # 1.10.1
    add(cat, "00001", batch=unicodedata.normalize("NFD", "Γάμος Βασίλη "))
    organizer.set_batch_year(cat, "Γάμος Βασίλη", "1994")
    assert files(cat) == ["1994/Γάμος Βασίλη/1994_γάμος-βασίλη_00001.jpg"]


def test_rename_batch(cat):
    add(cat, "00001")
    add(cat, "00002", batch="Κρήτη")
    organizer.set_batch_year(cat, "Κρήτη", "1994")
    organizer.rename_batch(cat, "Γάμος Βασίλη", "Κρήτη")  # an existing name merges the two
    assert organizer.batches(cat) == ["Κρήτη"]
    assert files(cat) == ["1994/Κρήτη/1994_κρήτη_00001.jpg", "1994/Κρήτη/1994_κρήτη_00002.jpg"]


def test_finds_a_photo_moved_by_hand(cat):
    add(cat, "00001")
    organizer.organize(cat)
    f = next(cat.root.rglob("*00001.jpg"))
    shutil.move(str(f), cat.root / f.name)
    organizer.set_batch_year(cat, "Γάμος Βασίλη", "1994")
    assert files(cat) == ["1994/Γάμος Βασίλη/1994_γάμος-βασίλη_00001.jpg"]
    assert organizer.problem_text() == ""


def test_says_why_a_photo_stays(cat):
    add(cat, "00001")
    add(cat, "00002", status="scanning")
    organizer.organize(cat)
    (cat.root / cat.rows("id = '00001'")[0]["path"]).unlink()
    organizer.organize(cat)
    assert "00001" in organizer.problem_text()
    report = organizer.batch_report(cat, "Γάμος Βασίλη")
    assert "1 wait for their backs" in report and "can't be found" in report


def test_photos_left_waiting_for_backs_are_filed(cat):
    add(cat, "00001", status="scanning", back_status="pending")
    add(cat, "00002", status="scanning", back_status="pending")
    assert organizer.finish_unfinished(cat, keep={"00002"}) == 1
    statuses = {r["id"]: (r["status"], r["back_status"]) for r in cat.rows()}
    assert statuses == {"00001": ("ready", "not scanned"), "00002": ("scanning", "pending")}


def test_naming_template(cat):
    add(cat, "00001")
    organizer.template = organizer.naming.parse(
        "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{#if MM}}{{MM}} - {{/if}}{{album}}/{{id}}")
    organizer.set_batch_year(cat, "Γάμος Βασίλη", "2007", 1)
    assert files(cat) == ["2007/01 - Γάμος Βασίλη/00001.jpg"]


def test_back_text():
    personal, lab, paper = organizer.split_back_text("Μαρία και Γιώργος 1994\nKODAK ENDURA PAPER\n0012 345 NN")
    assert "Μαρία" in personal and "KODAK" not in personal
    assert paper == "Kodak Endura"
    assert organizer.find_year("Πάσχα '94") == 1994
    assert organizer.find_year("no year here") is None


def test_print_size():
    assert organizer.print_size(3543, 2362, 600) == ("15.0x10.0", "10x15")
