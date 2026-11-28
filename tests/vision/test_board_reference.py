"""board_reference: вирівнювання фото дошки за 4 кутами на синтетичному кадрі."""

import cv2
import numpy as np

from rootbot.vision.board_reference import warp_to_board

BOARD_MM = (570, 440)  # board.size_mm
PX_PER_MM = 2.0
# білі кола на чорній «дошці», центри в мм
SPOTS_MM = [(100, 100), (450, 80), (300, 350)]
CORNERS_PX = [(120, 80), (700, 110), (760, 540), (60, 500)]  # (0,0), (W,0), (W,H), (0,H)


def photo():
    """Фото під кутом: дошка 1 px/мм, спроєктована так, що її кути лягли в CORNERS_PX."""
    w, h = BOARD_MM
    board = np.zeros((h, w, 3), np.uint8)
    for x, y in SPOTS_MM:
        cv2.circle(board, (x, y), 20, (255, 255, 255), -1)
    to_photo = cv2.getPerspectiveTransform(
        np.float32([(0, 0), (w, 0), (w, h), (0, h)]), np.float32(CORNERS_PX)
    )
    return cv2.warpPerspective(board, to_photo, (800, 600))


def brightness(ref, x_mm, y_mm):
    return int(ref[round(y_mm * PX_PER_MM), round(x_mm * PX_PER_MM)].mean())


def test_warp_gives_top_down_view_at_scale():
    ref = warp_to_board(photo(), CORNERS_PX, BOARD_MM, PX_PER_MM)
    assert ref.shape[:2] == (BOARD_MM[1] * PX_PER_MM, BOARD_MM[0] * PX_PER_MM)
    for x, y in SPOTS_MM:
        assert brightness(ref, x, y) > 200  # коло там, де й на дошці
    assert brightness(ref, 200, 250) < 50  # фон


def test_corner_order_sets_orientation():
    """Почали клікати з протилежного кута -> еталон повернутий на 180°."""
    rotated = CORNERS_PX[2:] + CORNERS_PX[:2]
    ref = warp_to_board(photo(), rotated, BOARD_MM, PX_PER_MM)
    w, h = BOARD_MM
    for x, y in SPOTS_MM:
        assert brightness(ref, w - x, h - y) > 200
        assert brightness(ref, x, y) < 50
