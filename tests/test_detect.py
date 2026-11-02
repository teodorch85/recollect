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
