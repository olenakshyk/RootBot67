"""Кандидати оглядових поз і жадібний вибір найкорисніших."""

from rootbot.common.types import BoardPoint
from rootbot.robot.scan_poses import candidate_poses, select_poses
from rootbot.vision.board_mask import BoardMask

START = [430.0, 20.0, 700.0, -176.0, 18.0, -83.0]


def test_candidates_around_start_never_lower():
    poses = dict(candidate_poses(START, shift_mm=80, lift_mm=100, tilt_deg=12))
    assert poses["start"] == START
    assert len(poses) == 1 + 8 + 1 + 4
    assert poses["shift_x+80_y-80"][:3] == [510.0, -60.0, 700.0]
    assert poses["lift_z+100"][2] == 800.0
    assert poses["tilt_pitch-12"][3:] == [-176.0, 6.0, -83.0]
    assert all(p[2] >= START[2] for p in poses.values())


def board():
    return BoardMask.whole((100, 100), 1).empty()


def square(x0, x1):
    """Смуга дошки x0..x1 мм на всю висоту."""
    mask = board()
    mask.grid[:, x0:x1] = True
    return mask


def test_greedy_adds_most_new_area_then_other_angles():
    masks = {
        "start": square(0, 50),
        "same": square(0, 50),  # нічого нового
        "right": square(50, 100),  # друга половина
        "middle": square(25, 75),
        "empty_view": board(),
    }
    clearings = {"start": [1], "same": [1, 2], "right": [3], "middle": [2], "empty_view": []}
    chosen = select_poses(masks, clearings, keep=3, first="start")
    # right додає 50 мм смуги; далі площі не додається -> поза з найбільшою к-стю галявин
    assert chosen == ["start", "right", "same"]


def test_stops_when_nothing_useful():
    masks = {"start": square(0, 100), "empty_view": board()}
    assert select_poses(masks, {"start": [1], "empty_view": []}, keep=7, first="start") == ["start"]


def test_circle_masks_work_too():
    m = board()
    masks = {"start": m.circle(BoardPoint(20, 20), 10), "b": m.circle(BoardPoint(80, 80), 10)}
    assert select_poses(masks, {"start": [], "b": []}, keep=2, first="start") == ["start", "b"]
