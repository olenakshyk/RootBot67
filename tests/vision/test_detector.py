"""Detector з фейковими дошкою й моделлю: галявини, зведення кадрів, причини перегляду."""

from pathlib import Path

import numpy as np
import pytest

from rootbot.common.types import BoardPoint, ClearingId, Faction, PieceKind
from rootbot.game.board import load_board
from rootbot.game.state import BOT
from rootbot.vision.board_detector import BoardHomography
from rootbot.vision.detector import Detector
from rootbot.vision.observation import BoardObservation, RecheckReason, RecheckRequest
from rootbot.vision.piece_detector_model import PieceBox

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"
BOARD = load_board(AUTUMN)
C1, C2 = ClearingId(1), ClearingId(2)
EYRIE = Faction.EYRIE
FRAME = np.zeros((10, 10, 3), dtype=np.uint8)  # вміст не важливий: дошку й рамки дають фейки


def view(x0=0.0, y0=0.0, w=600, h=600):
    """Кадр, де піксель = мм дошки зі зсувом (x0, y0): видно прямокутник x0..x0+w, y0..y0+h."""
    to_board = np.array([[1.0, 0, x0], [0, 1.0, y0], [0, 0, 1.0]])
    return BoardHomography(to_board, (w, h), (570.0, 440.0))


class FakeBoardDetector:
    board_size_mm = (570.0, 440.0)

    def __init__(self, views):
        self.views = list(views)

    def find(self, frame):
        return self.views.pop(0)


class FakeModel:
    def __init__(self, boxes_per_frame):
        self.boxes = boxes_per_frame
        self.calls = 0

    def detect(self, frames):
        self.calls += 1
        assert len(frames) == len(self.boxes)
        return self.boxes


def box_at(point: BoardPoint, faction=BOT, kind=PieceKind.WARRIOR, conf=0.9, v=None):
    """Рамка, у якої точка основи (середина нижнього краю) падає в `point` мм дошки."""
    x0, y0 = (v.to_board[0, 2], v.to_board[1, 2]) if v is not None else (0.0, 0.0)
    x, y = point.x - x0, point.y - y0
    return PieceBox(x - 8, y - 20, x + 8, y, faction, kind, conf)


def detector(views, boxes):
    return Detector(
        BOARD,
        FakeBoardDetector(views),
        FakeModel(boxes),
        min_confidence=0.4,
        sure_confidence=0.7,
        merge_mm=15,
        min_coverage=0.97,
        clearing_margin=0.04,
        mask_mm_per_px=2,
    )


def near(clearing, dx=0.0, dy=0.0):
    c = BOARD[clearing].center
    return BoardPoint(c.x + dx, c.y + dy)


def rounded(points):
    """Точки як відсортовані пари з округленням: порядок і похибка обчислень не важливі."""
    return sorted((round(p.x, 3), round(p.y, 3)) for p in points)


def scan_and_detect(d, n_frames=1, target=None):
    """add_frames -> detect, як робить Orchestrator, коли покриття вистачає з першого разу."""
    d.add_frames([FRAME] * n_frames, target=target)
    return d.detect()


def test_counts_and_positions_by_clearing():
    boxes = [
        box_at(near(C1)),
        box_at(near(C1, 20)),
        box_at(near(C2), EYRIE),
        box_at(near(C2, 10), conf=0.2),  # надто невпевнена — відкинута
    ]
    obs, recheck = scan_and_detect(detector([view()], [boxes]))
    assert obs.pieces[C1][BOT].warriors == 2 and obs.pieces[C2][EYRIE].warriors == 1
    assert len(obs.positions[C1][BOT]) == 2
    assert recheck is None


def test_piece_just_outside_the_circle_goes_to_the_nearest_clearing():
    edge = near(C1, BOARD[C1].radius_mm + 3)
    obs, _ = scan_and_detect(detector([view()], [[box_at(edge)]]))
    assert obs.pieces[C1][BOT].warriors == 1


def test_piece_outside_the_board_gets_no_clearing():
    obs, _ = scan_and_detect(detector([view()], [[box_at(BoardPoint(5, 450))]]))
    assert all(pieces == {} for pieces in obs.pieces.values())


def test_score_marker_on_the_board_gets_no_clearing():
    score = box_at(BoardPoint(100, 430), kind=PieceKind.SCORE)  # видно лише на першому кадрі
    d = detector([view(), view()], [[score], []])
    obs, recheck = scan_and_detect(d, n_frames=2)
    assert all(pieces == {} for pieces in obs.pieces.values())
    assert recheck is None


def test_same_piece_on_two_frames_is_one():
    p = near(C1)
    frames_boxes = [[box_at(p)], [box_at(BoardPoint(p.x + 3, p.y))]]
    obs, _ = scan_and_detect(detector([view(), view()], frames_boxes), n_frames=2)
    assert obs.pieces[C1][BOT].warriors == 1


# --- add_frames: покриття ---


def test_add_frames_says_what_is_not_visible_without_running_the_model():
    d = detector([view(w=300)], [[]])  # видно лише ліву половину дошки
    missing = d.add_frames([FRAME])
    assert missing.reasons == [RecheckReason.NOT_VISIBLE] and C1 not in missing.clearings
    assert all(p.x > 290 for p in missing.uncovered.centers())  # не видно праву частину
    assert d.model.calls == 0


def test_coverage_completes_over_two_calls_then_model_runs_once_on_all_frames():
    left, right = view(w=301), view(x0=200, w=600)
    d = detector([left, right], [[box_at(near(C1))], []])
    assert d.add_frames([FRAME]).reasons == [RecheckReason.NOT_VISIBLE]
    assert d.add_frames([FRAME]) is None  # тепер видно всю дошку
    obs, _ = d.detect()
    assert d.model.calls == 1  # модель — раз, на обох кадрах
    assert obs.pieces[C1][BOT].warriors == 1


def test_97_percent_of_target_is_enough():
    d = detector([view(w=564)], [[]])  # не дістає ~1 % дошки праворуч
    assert d.add_frames([FRAME]) is None


def test_target_can_be_one_clearing():
    c1 = BOARD[C1]
    edge = c1.radius_mm * 1.04 + 2
    small = view(w=int(c1.center.x + edge), h=int(c1.center.y + edge))  # лише галявина 1
    d = detector([small], [[box_at(near(C1))]])
    assert d.add_frames([FRAME], target=d.clearings_mask([C1])) is None
    obs, recheck = d.detect()
    assert obs.pieces[C1][BOT].warriors == 1 and recheck is None


def test_frame_without_board_is_skipped():
    d = detector([None], [[]])
    assert d.add_frames([FRAME]).reasons == [RecheckReason.NOT_VISIBLE]


def test_new_scan_starts_after_detect():
    d = detector([view(), view(w=300)], [[]])
    scan_and_detect(d)
    assert d.add_frames([FRAME]) is not None  # старі кадри не рахуються: новий огляд


# --- detect ---


def test_detect_with_incomplete_coverage_uses_what_is_visible():
    d = detector([view(w=300)], [[]])
    d.add_frames([FRAME])
    obs, _ = d.detect()
    assert obs.pieces and d.model.calls == 1


def test_frames_that_disagree_ask_to_recheck():
    frames_boxes = [[box_at(near(C1)), box_at(near(C1, 20))], [box_at(near(C1))]]
    _, recheck = scan_and_detect(detector([view(), view()], frames_boxes), n_frames=2)
    assert recheck == RecheckRequest([C1], [RecheckReason.DISAGREE])


def test_low_confidence_asks_to_recheck():
    _, recheck = scan_and_detect(detector([view()], [[box_at(near(C2), conf=0.5)]]))
    assert recheck == RecheckRequest([C2], [RecheckReason.LOW_CONFIDENCE])


def test_disagree_and_low_confidence_go_into_one_recheck():
    frames_boxes = [
        [box_at(near(C1)), box_at(near(C1, 20)), box_at(near(C2), conf=0.5)],
        [box_at(near(C1)), box_at(near(C2), conf=0.5)],
    ]
    _, recheck = scan_and_detect(detector([view(), view()], frames_boxes), n_frames=2)
    assert recheck == RecheckRequest(
        [C1, C2], [RecheckReason.DISAGREE, RecheckReason.LOW_CONFIDENCE]
    )


def test_keep_sets_keep_clearing():
    obs, _ = scan_and_detect(detector([view()], [[box_at(near(C2), kind=PieceKind.KEEP)]]))
    assert obs.keep_clearing == C2


def test_occupied_has_every_piece_on_the_clearing_with_the_keep():
    bot, enemy, keep = near(C2), near(C2, 20), near(C2, -20)
    boxes = [box_at(bot), box_at(enemy, EYRIE), box_at(keep, kind=PieceKind.KEEP)]
    obs, _ = scan_and_detect(detector([view()], [boxes]))
    assert rounded(obs.occupied[C2]) == rounded([bot, enemy, keep])
    assert rounded(obs.positions[C2][BOT]) == rounded([bot])
    assert obs.occupied[C1] == []


# --- refine ---


def test_refine_before_detect_is_an_error():
    d = detector([view()], [[]])
    with pytest.raises(RuntimeError):
        d.refine(BoardObservation(), [FRAME])


def test_refine_updates_only_fully_seen_clearings():
    d = detector([view(), view()], [[box_at(near(C1)), box_at(near(C2), EYRIE)]])
    obs, _ = scan_and_detect(d)
    # новий кадр бачить лише галявину 1 (ліва верхня частина дошки), і кота там уже немає
    c1 = BOARD[C1]
    edge = c1.radius_mm + 5
    d.board_detector.views = [view(w=int(c1.center.x + edge), h=int(c1.center.y + edge))]
    d.model.boxes = [[]]
    refined, recheck = d.refine(obs, [FRAME])
    assert refined.pieces[C1] == {}
    assert refined.pieces[C2][EYRIE].warriors == 1  # не видно — лишилось як було
    assert obs.pieces[C1][BOT].warriors == 1  # оригінал не змінено
    assert recheck is None


def test_refine_replaces_occupied_only_for_fully_seen_clearings():
    d = detector([view(), view()], [[box_at(near(C1)), box_at(near(C2), EYRIE)]])
    obs, _ = scan_and_detect(d)
    # новий кадр бачить лише галявину 1, де кота вже немає, а стоїть чужа фігурка
    c1 = BOARD[C1]
    edge = c1.radius_mm + 5
    d.board_detector.views = [view(w=int(c1.center.x + edge), h=int(c1.center.y + edge))]
    d.model.boxes = [[box_at(near(C1, 10), EYRIE)]]
    refined, _ = d.refine(obs, [FRAME])
    assert rounded(refined.occupied[C1]) == rounded([near(C1, 10)])
    assert rounded(refined.occupied[C2]) == rounded([near(C2)])  # не видно — лишилось як було
    assert rounded(obs.occupied[C1]) == rounded([near(C1)])  # оригінал не змінено


def test_refine_clears_keep_when_its_clearing_is_seen_without_it():
    d = detector([view(), view()], [[box_at(near(C2), kind=PieceKind.KEEP)]])
    obs, _ = scan_and_detect(d)
    d.model.boxes = [[]]
    refined, _ = d.refine(obs, [FRAME])
    assert refined.keep_clearing is None


def test_refine_keeps_keep_when_its_clearing_is_not_seen():
    d = detector([view(), view()], [[box_at(near(C2), kind=PieceKind.KEEP)]])
    obs, _ = scan_and_detect(d)
    c1 = BOARD[C1]
    edge = c1.radius_mm + 5
    d.board_detector.views = [view(w=int(c1.center.x + edge), h=int(c1.center.y + edge))]
    d.model.boxes = [[]]
    refined, _ = d.refine(obs, [FRAME])
    assert refined.keep_clearing == C2
