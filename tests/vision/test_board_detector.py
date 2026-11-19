"""board_detector (V1) на синтетичних кадрах: еталон з випадковою текстурою, кадр — його
перспективна проєкція з відомою гомографією. Файли поза git не потрібні."""

import cv2
import numpy as np
import pytest

from rootbot.common.types import BoardPoint
from rootbot.vision.board_detector import BoardDetector

BOARD_MM = (580, 440)
PX_PER_MM = 1.0


@pytest.fixture(scope="module")
def reference():
    """«Дошка»: шум + випадкові фігури, щоб було за що зачепитися SIFT."""
    rng = np.random.default_rng(0)
    w, h = int(BOARD_MM[0] * PX_PER_MM), int(BOARD_MM[1] * PX_PER_MM)
    img = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (5, 5), 0)
    for _ in range(150):
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), int(rng.integers(5, 30)), color, -1)
        else:
            cv2.rectangle(
                img, (x, y), (x + int(rng.integers(5, 40)), y + int(rng.integers(5, 40))), color, -1
            )
    return img


@pytest.fixture(scope="module")
def detector(reference):
    return BoardDetector(
        reference, PX_PER_MM, ratio_test=0.75, ransac_reproj_px=3.0, min_inliers=40
    )


def camera_frame(reference, corners_px, size=(800, 600)):
    """Кадр, де кути еталона потрапили в пікселі corners_px (перспектива), фон — інший шум."""
    h, w = reference.shape[:2]
    to_frame = cv2.getPerspectiveTransform(
        np.float32([(0, 0), (w, 0), (w, h), (0, h)]), np.float32(corners_px)
    )
    rng = np.random.default_rng(1)
    frame = cv2.GaussianBlur(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8), (9, 9), 0)
    warped = cv2.warpPerspective(reference, to_frame, size)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), to_frame, size) > 0
    frame[mask] = warped[mask]
    return frame, to_frame


def board_point_of(to_frame, x, y):
    p = np.linalg.inv(to_frame) @ np.array([x, y, 1.0])
    return p[0] / p[2] / PX_PER_MM, p[1] / p[2] / PX_PER_MM


def test_finds_board_under_perspective(reference, detector):
    frame, to_frame = camera_frame(reference, [(120, 80), (700, 110), (760, 540), (60, 500)])
    found = detector.find(frame)
    assert found is not None
    for x, y in [(200, 200), (400, 300), (650, 450)]:
        expected = board_point_of(to_frame, x, y)
        got = found.to_board_mm(x, y)
        assert np.hypot(got.x - expected[0], got.y - expected[1]) < 1.0  # мм


def test_partial_view_sees_only_part_of_board(reference, detector):
    # кадр показує лише ліву половину дошки, збільшено
    frame, _ = camera_frame(reference, [(-100, -50), (1100, -50), (1100, 850), (-100, 850)])
    found = detector.find(frame)
    assert found is not None
    assert found.sees(BoardPoint(150, 200), radius_mm=30)
    assert not found.sees(BoardPoint(500, 200), radius_mm=30)  # поза кадром
    assert not found.sees(BoardPoint(150, 15), radius_mm=30)  # коло вилазить за край кадру


def test_no_board_on_frame(detector):
    noise = np.random.default_rng(5).integers(0, 255, (600, 800, 3), dtype=np.uint8)
    assert detector.find(noise) is None
