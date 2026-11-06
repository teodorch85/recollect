"""Finding prints on the bed: prints lying edge to edge are told apart; single prints are never cut."""
import cv2
import numpy as np
import pytest

import detect
import fakes

DPI = 150
H, W = int(11.7 * DPI), int(8.5 * DPI)
EMPTY = fakes.lid(H, W, seed=9)

TOUCHING = {  # (print height, width), centre x, centre y, tilt: one print right below (or beside) the other
    "narrower below": [((585, 885), 640, 400, 0), ((585, 850), 630, 985, 0)],
    "tilted below": [((585, 885), 640, 400, 0), ((585, 885), 650, 990, 1.2)],
    "side by side": [((885, 585), 330, 700, 0), ((870, 585), 915, 710, 0)],
    "same size, shifted": [((585, 885), 640, 400, 0), ((585, 885), 665, 985, 0)],
}
SINGLE = {
    "sky across": [((885, 590), 640, 700, 0, True)],
    "13x18 with sky": [((1063, 768), 640, 700, 0.5, True)],
}


def scene(prints):
    img, truth = fakes.lid(H, W), []
    for n, (hw, x, y, a, *sky) in enumerate(prints):
        truth.append(fakes.place(img, fakes.picture(*hw, seed=n, sky=bool(sky and sky[0])), x, y, a))
    return img, truth


@pytest.mark.parametrize("name", TOUCHING)
def test_prints_lying_edge_to_edge_are_two(name):
    img, truth = scene(TOUCHING[name])
    found = detect.find_photos(img, DPI, EMPTY)
    assert len(found) == 2
    for (c, size, a), (tc, tsize, ta) in zip(found, truth):
        assert np.hypot(c[0] - tc[0], c[1] - tc[1]) < 6
        assert all(abs(s - t) <= 8 for s, t in zip(size, tsize))  # a mm or so may go at the seam
        assert abs(a - ta) < 0.6


@pytest.mark.parametrize("name", SINGLE)
def test_one_print_stays_one(name):
    img, _ = scene(SINGLE[name])
    assert len(detect.find_photos(img, DPI, EMPTY)) == 1


def test_a_turned_photo_is_drawn_turned():
    img, truth = scene([((585, 885), 640, 700, 0)])
    still = detect.draw_overlay(img, truth, max_side=700, turns=[0])
    turned = detect.draw_overlay(img, truth, max_side=700, turns=[90])
    assert np.array_equal(still, detect.draw_overlay(img, truth, max_side=700))
    f = 700 / H
    (cx, cy), (w, h), _ = truth[0]
    box = turned[int(cy * f - h * f / 2) + 3:int(cy * f + h * f / 2) - 3, int(cx * f - w * f / 2) + 3:int(cx * f - h * f / 2) - 3]
    assert (np.abs(box.astype(int) - detect.TURNED_BG).sum(axis=2) < 10).mean() > 0.9  # space beside the upright photo


def strip_scene(mm):
    """Two big prints, one right above the other. The top one lies `mm` from the left edge of the glass, with
    the lid beside it a little in shadow; the bottom one touches that edge."""
    left = int(mm / 25.4 * DPI)
    img = fakes.lid(H, W)
    img[105:695, :left] = img[105:695, :left] - 12
    return img, [fakes.place(img, fakes.picture(590, 885, seed=0), left + 442, 400),
                 fakes.place(img, fakes.picture(590, 885, seed=1), 442, 990)]


@pytest.mark.parametrize("refine", [True, False])
@pytest.mark.parametrize("mm", [6, 14])
def test_lid_between_the_glass_edge_and_a_stacked_print_is_trimmed(mm, refine):
    img, truth = strip_scene(mm)
    found = detect.find_photos(img, DPI, EMPTY, refine=refine)
    assert len(found) == 2
    for (c, size, a), (tc, tsize, ta) in zip(found, truth):
        assert np.hypot(c[0] - tc[0], c[1] - tc[1]) < 6
        assert all(abs(s - t) <= 8 for s, t in zip(size, tsize))  # the top box was 40-70 px too wide
        assert abs(a - ta) < 0.6


def test_plain_sky_along_a_side_is_not_taken_for_lid():
    img = fakes.lid(H, W)
    p = fakes.picture(885, 590, seed=3, sky=True)
    p[:60] = (235, 235, 235)  # a band of white sky, 10 mm of it, along the whole top
    truth = fakes.place(img, p, 640, 700)
    (found,) = detect.find_photos(img, DPI, EMPTY)
    assert all(abs(s - t) <= 8 for s, t in zip(sorted(found[1]), sorted(truth[1])))


def test_a_print_keeps_two_inches_whatever_is_trimmed():
    img = fakes.lid(H, W)
    p = fakes.picture(300, 300, seed=4)
    p[:, :40] = 228  # lid-like left strip on a small print: trimming 40 px would leave under 2 in
    rect = fakes.place(img, p, 640, 700)
    assert detect._trim_lid(img, rect, DPI, 228.0) == rect


def shadowed_gap(v, gap=4):
    """Two big prints one above the other with a narrow gap whose lid is only `v` bright (in their shadow)."""
    img = fakes.lid(H, W)
    img[692:695 + gap + 3, 158:1042] = v  # just the gap, between the two prints' sides
    return img, [fakes.place(img, fakes.picture(590, 885, seed=0), 600, 400),
                 fakes.place(img, fakes.picture(590, 885, seed=1), 600, 400 + 590 + gap)]


def test_finer_settings_find_prints_with_a_dim_narrow_gap():
    img, truth = shadowed_gap(165)
    assert len(detect.find_photos(img, DPI, EMPTY)) == 1  # the gap is too dark for the normal settings
    found = detect.find_photos(img, DPI, EMPTY, finer=True)
    assert len(found) == 2
    for (c, size, a), (tc, tsize, ta) in zip(found, truth):
        assert np.hypot(c[0] - tc[0], c[1] - tc[1]) < 8 and all(abs(s - t) <= 10 for s, t in zip(size, tsize))


def test_finer_settings_find_prints_with_a_one_millimetre_step():
    img = fakes.lid(H, W)
    fakes.place(img, fakes.picture(590, 885, seed=0), 600, 400)
    fakes.place(img, fakes.picture(590, 885, seed=1), 600 + 7, 400 + 590)  # 1.2 mm to the side, touching
    assert len(detect.find_photos(img, DPI, EMPTY)) == 1
    assert len(detect.find_photos(img, DPI, EMPTY, finer=True)) == 2


def test_finer_settings_leave_single_prints_whole():
    for name in SINGLE:
        img, _ = scene(SINGLE[name])
        assert len(detect.find_photos(img, DPI, EMPTY, finer=True)) == 1


@pytest.mark.parametrize("name", list(TOUCHING) + list(SINGLE))
def test_find_prints_is_sure_about_prints_it_split_or_left_whole(name):
    img, _ = scene((TOUCHING | SINGLE)[name])
    rects, unsure = detect.find_prints(img, DPI, EMPTY)
    assert rects == detect.find_photos(img, DPI, EMPTY) and unsure == []


def test_find_prints_doubts_a_step_it_is_not_sure_of():
    img = fakes.lid(H, W)
    fakes.place(img, fakes.picture(590, 885, seed=0), 600, 400)
    fakes.place(img, fakes.picture(590, 885, seed=1), 600 + 7, 400 + 590)
    rects, unsure = detect.find_prints(img, DPI, EMPTY)
    assert len(rects) == 1 and unsure == [0]  # taken for one, but the finer settings see two
    rects, unsure = detect.find_prints(img, DPI, EMPTY, finer=True)
    assert len(rects) == 2 and unsure == []


def test_a_rect_that_could_be_two_prints_but_is_no_print_size_is_odd():
    mm = DPI / 25.4
    assert not detect.odd_size(((0, 0), (150 * mm, 100 * mm), 0), DPI)    # 10x15
    assert not detect.odd_size(((0, 0), (200 * mm, 150 * mm), 0), DPI)    # 15x20
    assert detect.odd_size(((0, 0), (230 * mm, 150 * mm), 0), DPI)        # 15x23: big, no such print
    assert detect.odd_size(((0, 0), (300 * mm, 100 * mm), 0), DPI)        # 10x30: two 10x15 end to end
    assert not detect.odd_size(((0, 0), (110 * mm, 80 * mm), 0), DPI)     # 8x11 standard
    assert not detect.odd_size(((0, 0), (120 * mm, 75 * mm), 0), DPI)     # small and odd: no reason to doubt


def grid_of_9x13(dpi):
    """Four 9x13 prints in two rows, 1 cm apart, the last one a little tilted: an ordinary bed."""
    cm = dpi / 2.54
    img = fakes.lid(int(11.7 * dpi), int(8.5 * dpi))
    for n, (x, y, a) in enumerate([(5.8, 7.5, 0), (15.8, 7.5, 0), (5.8, 21.5, 0), (15.8, 21.5, 0.8)]):
        fakes.place(img, fakes.picture(int(13 * cm), int(9 * cm), seed=n), x * cm, y * cm, a)
    return img, fakes.lid(*img.shape[:2], seed=9)


@pytest.mark.parametrize("dpi", [150, 300])
def test_an_ordinary_grid_of_small_prints_raises_no_doubt(dpi):
    """The outline of a single print jogs by a pixel here and there: that hinted at two prints, and the finer
    settings split one in two, so every such bed opened the Check prints window."""
    img, empty = grid_of_9x13(dpi)
    rects, unsure = detect.find_prints(img, dpi, empty)
    assert len(rects) == 4 and unsure == []
    assert len(detect.find_photos(img, dpi, empty, finer=True)) == 4  # "Find prints again" keeps them whole


def test_grey_sky_along_a_side_is_not_trimmed_away():
    """Lid-grey sky with a horizon 8 mm in looks like a strip of lid; away from the glass edge it stays."""
    img = fakes.lid(H, W)
    p = fakes.picture(590, 885, seed=5)
    p[:47] = (222, 214, 208)
    truth = fakes.place(img, p, 640, 700)
    (found,) = detect.find_photos(img, DPI, EMPTY)
    assert all(abs(s - t) <= 14 for s, t in zip(found[1], truth[1]))  # it lost 45 px of sky


@pytest.mark.parametrize("sky", [(222, 214, 208), (225, 215, 200)])
def test_sky_of_a_print_against_the_glass_edge_is_not_trimmed(sky):
    """Prints are usually pushed into the corner of the glass: a plain strip of sky along the side that
    touches the edge is still picture, not lid (lid matches the empty-bed scan in colour, sky does not)."""
    img = fakes.lid(H, W)
    p = fakes.picture(590, 885, seed=5)
    p[:47] = sky  # 8 mm of plain sky, then the picture
    truth = fakes.place(img, p, 443, 296)  # top-left corner of the glass
    for refine in (True, False):
        (found,) = detect.find_photos(img, DPI, EMPTY, refine=refine)
        assert all(abs(s - t) <= 8 for s, t in zip(found[1], truth[1])), (refine, found)
