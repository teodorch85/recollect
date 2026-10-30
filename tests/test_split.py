"""Finding the cut between two prints saved as one photo, and cutting each out along its edges."""
import cv2
import numpy as np
import pytest

import enhance
import fakes
import split

DPI = 300  # half the usual: the same shapes, quicker tests


def scene(kind):
    """A merged scan of two prints, as saved (colour restored, sharpened), and where the prints really are."""
    h, w = 1350, 2500
    if kind == "stacked":
        h, w = w, h
    img = fakes.lid(h, w)
    if kind == "touching, tilted":
        prints = [fakes.place(img, fakes.picture(1180, 1170, 1), 625, 675, 3),
                  fakes.place(img, fakes.picture(1180, 1170, 2), 1850, 675, -2.5)]
    elif kind == "white borders":
        prints = [fakes.place(img, fakes.picture(1150, 1150, 1, border=45), 600, 675, 1),
                  fakes.place(img, fakes.picture(1150, 1150, 2, border=45), 1790, 675, 0.5)]
    elif kind == "stacked":
        prints = [fakes.place(img, fakes.picture(1150, 1250, 1), 675, 610, 2),
                  fakes.place(img, fakes.picture(1150, 1250, 2), 675, 1850, -1)]
    else:  # different sizes
        prints = [fakes.place(img, fakes.picture(1200, 1150, 1), 625, 675, 0),
                  fakes.place(img, fakes.picture(850, 1150, 2), 1850, 550, 4)]
    return enhance.process_front(img, DPI, "High", True), prints


@pytest.mark.parametrize("kind", ["touching, tilted", "white borders", "stacked", "different sizes"])
def test_finds_both_prints(kind):
    img, truth = scene(kind)
    got = split.boxes(img, split.find_cut(img), DPI)
    for (c, size, angle), (gc, gsize, gangle) in zip(truth, got):
        assert np.hypot(c[0] - gc[0], c[1] - gc[1]) < 4
        assert np.allclose(sorted(size), sorted(gsize), atol=4)
        assert abs(angle - gangle) < 0.3


def test_cut_pieces_are_the_prints():
    img, truth = scene("different sizes")
    first, second = split.cut(img, split.find_cut(img), DPI)
    inset = 2 * round(0.3 / 25.4 * DPI)  # a hair off each edge, so no lid shows
    assert abs(first.shape[0] - 1200 + inset) <= 4 and abs(first.shape[1] - 1150 + inset) <= 4
    assert abs(second.shape[0] - 850 + inset) <= 4 and abs(second.shape[1] - 1150 + inset) <= 4


def test_click_near_the_gap_finds_it():
    img, _ = scene("touching, tilted")
    (x0, _), (x1, _) = split.find_cut(img, near=(0.5, 0.7))
    assert 0.47 < x0 < 0.53 and 0.47 < x1 < 0.53


def test_a_drawn_line_reaches_the_edges():
    assert split.line_through((0.5, 0.2), (0.5, 0.8)) == ((0.5, 0.0), (0.5, 1.0))
    (x0, y0), (x1, y1) = split.line_through((0.1, 0.5), (0.9, 0.5))
    assert (x0, x1) == (0.0, 1.0) and y0 == pytest.approx(0.5) and y1 == pytest.approx(0.5)
    assert split.line_through((0.3, 0.3), (0.3, 0.3)) is None  # a click, not a line


def test_back_halves_follow_the_same_line():
    back = np.full((1000, 2000, 3), 240, np.uint8)
    cv2.rectangle(back, (1500, 400), (1700, 600), (0, 0, 0), -1)  # writing on the right-hand back only
    left, right = split.cut_back(back, ((0.5, 0.0), (0.5, 1.0)))
    assert left.shape[:2] == right.shape[:2] == (1000, 1000)
    assert left.min() == 240 and right.min() == 0
