"""The Check prints window's edits (no window needed): remove, split, look again, undo."""
import checkprints
from checkprints import Boxes, count_text

A, B, C = ((100, 100), (50, 80), 0), ((300, 100), (50, 80), 0), ((500, 100), (50, 80), 0)


def test_unsure_boxes_follow_their_prints_through_edits():
    boxes = Boxes([A, B, C], unsure=[1])
    assert boxes.rects == [A, B, C] and boxes.unsure == [1]
    boxes.remove(0)
    assert boxes.rects == [B, C] and boxes.unsure == [0]
    boxes.split(0, [A, C])  # B was two prints: they take its place, and are not in doubt
    assert boxes.rects == [A, C, C] and boxes.unsure == []


def test_every_change_can_be_undone_in_turn():
    boxes = Boxes([A, B], unsure=[0])
    boxes.remove(1)
    boxes.replace_all([C], [0])
    assert boxes.undo() and boxes.rects == [A] and boxes.unsure == [0]
    assert boxes.undo() and boxes.rects == [A, B]
    assert not boxes.undo()  # nothing left to take back


def test_the_question_at_the_top():
    assert count_text(1) == "1 print found. Is that right?"
    assert count_text(5) == "5 prints found. Is that right?"
    assert "nothing to save" in count_text(0)
    assert checkprints.CheckPrints  # the window class imports without a display
