"""The order prints were taken in, from the roll and frame numbers labs print on the backs."""
import pytest

import organizer
import sequence


@pytest.mark.parametrize("text, found", [
    ("0123 NNN 12", ("123", 12)),
    ("12 N N +1 0045", ("45", 12)),
    ("<12> 0045", ("45", 12)),
    ("N12 +1 -1 0045", ("45", 12)),
    ("0045 NNN 012", ("45", 12)),
    ("IMG_0456.JPG", ("IMG", 456)),
    ("DSC01234.JPG 10x15", ("DSC", 1234)),
    ("Κρήτη 1994", ("", None)),         # personal writing is not a frame
    ("15/7/94 with Maria", ("", None)),
    ("", ("", None)),
])
def test_roll_and_frame(text, found):
    assert sequence.parse_lab(text) == found


def test_lab_lines_are_told_from_writing():
    personal, lab, _ = organizer.split_back_text("Κρήτη 1994\n<12> 0045\nN12 +1 -1 0045")
    assert personal == "Κρήτη 1994" and lab == "<12> 0045\nN12 +1 -1 0045"


def rows(*labs):
    return [{"id": f"{n + 1:05d}", "back_lab": lab} for n, lab in enumerate(labs)]


def test_order_follows_the_film():
    # scanned shuffled: frames 14, 12, (blank back), 13; then a second roll
    order = sequence.order(rows("0045 NNN 14", "0045 NNN 12", "", "0045 NNN 13", "0046 NNN 2", "0046 NNN 1"))
    taken = sorted(order, key=order.get)
    assert taken == ["00002", "00003", "00004", "00001", "00006", "00005"]  # the blank one stays after 12


def test_without_lab_lines_the_scan_order_stays():
    order = sequence.order(rows("", "", ""))
    assert order == {"00001": 1, "00002": 2, "00003": 3}
