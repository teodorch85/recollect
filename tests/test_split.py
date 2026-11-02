"""Finding the cut between two prints saved as one photo, and cutting each out along its edges."""
import cv2
import numpy as np
import pytest

import detect
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


@pytest.mark.parametrize("tilt", [0, 2.0])
def test_a_dragged_line_on_a_rect_gives_the_two_prints_on_the_bed(tilt):
    """The Check prints window: two prints taken for one; the line is dragged on the bed, the rects are on the bed."""
    dpi, h, w = 150, 1755, 1275
    img = fakes.lid(h, w)
    a = np.radians(tilt)
    along = np.array([np.sin(a), np.cos(a)])  # the block's long axis, as the canvas sees it
    centre = np.array([640.0, 900.0])
    truth = [fakes.place(img, fakes.picture(590, 885, seed=n), *(centre + s * 295 * along), tilt)
             for n, s in enumerate((-1, 1))]
    (merged,) = detect.find_photos(img, dpi, fakes.lid(h, w, seed=9))  # no step, no gap: taken for one
    across = np.array([np.cos(a), -np.sin(a)])
    p, q = centre - 300 * across, centre + 300 * across
    got = split.rect_in_two(img, merged, dpi, tuple(p), tuple(q))
    assert len(got) == 2
    for (c, size, angle), (tc, tsize, tangle) in zip(got, truth):
        assert np.hypot(c[0] - tc[0], c[1] - tc[1]) < 6
        assert all(abs(s - t) <= 10 for s, t in zip(size, tsize))
        assert abs(angle - tangle) < 0.8
    assert split.rect_in_two(img, merged, dpi, tuple(p), tuple(p)) is None


def test_a_drag_along_the_edge_or_across_a_corner_is_no_cut():
    """A line just inside the box's edge gave a piece of negative height, and one across a corner a sliver:
    saving those failed (or filed a scrap of lid as a photo)."""
    dpi, h, w = 150, 1755, 1275
    img = fakes.lid(h, w)
    fakes.place(img, fakes.picture(590, 885, seed=1), 640, 500)
    fakes.place(img, fakes.picture(760, 885, seed=2), 640, 500 + 295 + 380)
    (merged,) = detect.find_photos(img, dpi, fakes.lid(h, w, seed=9))
    (cx, cy), (bw, bh), _ = merged
    top, left = cy - bh / 2, cx - bw / 2
    assert split.rect_in_two(img, merged, dpi, (100, top + 3), (1200, top + 3)) is None
    assert split.rect_in_two(img, merged, dpi, (100, top + 40), (1200, top + 40)) is None
    assert split.rect_in_two(img, merged, dpi, (left - 20, top + 60), (left + 60, top - 20)) is None
    assert len(split.rect_in_two(img, merged, dpi, (100, 795), (1200, 795))) == 2


def test_crop_coordinates_round_trip():
    rect = ((500.0, 700.0), (400.0, 300.0), 7.0)
    x, y = split.to_crop(rect, rect[0])
    assert (x, y) == pytest.approx((0.5, 0.5))
    box = ((200.0, 150.0), (100.0, 80.0), 0.0)  # the middle of the crop, in crop pixels
    assert split.from_crop(rect, box)[0] == pytest.approx(rect[0], abs=1e-6)
    assert split.from_crop(rect, box)[2] == pytest.approx(7.0)
