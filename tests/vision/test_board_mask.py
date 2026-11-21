"""BoardMask: маски дошки й покриття кадрами."""

import numpy as np

from rootbot.common.types import BoardPoint
from rootbot.vision.board_detector import BoardHomography
from rootbot.vision.board_mask import BoardMask

BOARD_MM = (580.0, 440.0)


def view(x0=0.0, w=600, h=600):
    to_board = np.array([[1.0, 0, x0], [0, 1.0, 0], [0, 0, 1.0]])
    return BoardHomography(to_board, (w, h), BOARD_MM)


def test_whole_board_coverage():
    board = BoardMask.whole(BOARD_MM, 2)
    assert board.coverage([view()]) == 1.0
    assert abs(board.coverage([view(w=290)]) - 0.5) < 0.01
    assert board.coverage([]) == 0.0


def test_frames_add_up():
    board = BoardMask.whole(BOARD_MM, 2)
    assert board.coverage([view(w=300), view(x0=280, w=320)]) == 1.0


def test_circle_and_union():
    board = BoardMask.whole(BOARD_MM, 2)
    a = board.circle(BoardPoint(100, 100), 20)
    b = board.circle(BoardPoint(400, 300), 20)
    both = a | b
    assert both.area() == a.area() + b.area()
    assert abs(both.coverage([view(w=200)]) - 0.5) < 0.05  # видно лише перше коло


def test_uncovered_part_and_its_centers():
    board = BoardMask.whole(BOARD_MM, 2)
    uncovered = board - board.visible_on([view(w=290)])
    (center,) = uncovered.centers()
    assert abs(center.x - 435) < 3 and abs(center.y - 220) < 3
