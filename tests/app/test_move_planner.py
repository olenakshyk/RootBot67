"""MovePlanner: яких котів брати й куди ставити."""

import math
from pathlib import Path

import pytest

from rootbot.app.move_planner import MovePlanner, PiecePositionUnknown
from rootbot.common.types import BoardPoint, ClearingId, Faction
from rootbot.game.actions import Move, Recruit
from rootbot.game.board import load_board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY
from rootbot.vision.observation import BoardObservation

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"
C1, C2 = ClearingId(1), ClearingId(2)
SPACING = 30
BOARD_MM = (570.0, 440.0)


def observation(positions, keeps=None):
    """Огляд, де occupied — усі фігурки з positions і точки Keep з keeps."""
    keeps = keeps or {}
    clearings = set(positions) | set(keeps)
    occupied = {
        c: [q for points in positions.get(c, {}).values() for q in points] + keeps.get(c, [])
        for c in clearings
    }
    return BoardObservation(
        positions={c: positions.get(c, {}) for c in clearings}, occupied=occupied
    )


def planner(positions=None):
    p = MovePlanner(load_board(AUTUMN), BOARD_MM, [], spacing_mm=SPACING)
    p.process(observation(positions or {}))
    return p


def far_enough(point, others):
    return all(math.dist((point.x, point.y), (q.x, q.y)) >= SPACING for q in others)


def test_takes_known_bot_positions_first():
    cat = BoardPoint(100, 100)
    p = planner({C1: {BOT: [cat]}})
    assert p.move(Move(C1, C2, 1))[0].source == cat


def test_full_scan_keeps_unseen_clearings():
    old_c1, old_c2, new_c2 = BoardPoint(50, 50), BoardPoint(310, 40), BoardPoint(320, 45)
    p = planner({C1: {BOT: [old_c1]}, C2: {BOT: [old_c2]}})
    p.process(observation({C2: {BOT: [new_c2]}}))
    assert p.remove(C1, 1)[0].source == old_c1
    assert p.remove(C2, 1)[0].source == new_c2


def test_full_scan_keeps_planned_spots_of_unseen_clearings():
    p = planner()
    placed = p.recruit(Recruit(C1, 1))[0].target
    p.process(observation({C2: {}}))
    assert p.remove(C1, 1)[0].source == placed


def test_take_without_known_position_raises():
    p = planner({C1: {BOT: [BoardPoint(50, 50)]}})
    with pytest.raises(PiecePositionUnknown):
        p.move(Move(C1, C2, 2))
    with pytest.raises(PiecePositionUnknown):
        p.remove(C2, 1)


def test_free_spot_avoids_other_pieces():
    p = planner()
    center = p.board[C2].center
    p.process(observation({C2: {Faction.EYRIE: [center]}}))
    spot = p.free_spot(C2)
    assert spot != center and far_enough(spot, [center])


def test_free_spot_avoids_keep():
    p = planner()
    keep = p.board[C2].center
    p.process(observation({C2: {}}, keeps={C2: [keep]}))
    targets = [t.target for t in p.recruit(Recruit(C2, 3))]
    assert all(far_enough(t, [keep]) for t in targets)


def test_free_spot_avoids_enemy_and_keep():
    p = planner()
    c = p.board[C2].center
    keep, enemy = BoardPoint(c.x + 10, c.y), BoardPoint(c.x - 15, c.y + 10)
    p.process(observation({C2: {Faction.EYRIE: [enemy]}}, keeps={C2: [keep]}))
    assert far_enough(p.free_spot(C2), [keep, enemy])


def clearance(point, others):
    return min(math.dist((point.x, point.y), (q.x, q.y)) for q in others)


def packed(center, reach):
    step = SPACING * 2 / 3
    n = math.ceil(reach / step)
    nodes = (
        BoardPoint(center.x + i * step, center.y + j * step)
        for i in range(-n, n + 1)
        for j in range(-n, n + 1)
    )
    return [q for q in nodes if math.dist((q.x, q.y), (center.x, center.y)) <= reach]


def test_packed_clearing_gives_spot_just_outside_it():
    p = planner()
    c = p.board[C2]
    pieces = packed(c.center, c.radius_mm)
    p.process(observation({C2: {Faction.EYRIE: pieces}}))
    spot = p.free_spot(C2)
    own = math.dist((spot.x, spot.y), (c.center.x, c.center.y))
    assert own > c.radius_mm and far_enough(spot, pieces)
    assert 0 <= spot.x <= BOARD_MM[0] and 0 <= spot.y <= BOARD_MM[1]
    for other in p.board.clearings.values():
        if other.id != C2:
            assert own < math.dist((spot.x, spot.y), (other.center.x, other.center.y))


def test_packed_surroundings_give_least_crowded_spot():
    p = planner()
    c = p.board[C2]
    pieces = packed(c.center, 200)
    p.process(observation({C2: {Faction.EYRIE: pieces}}))
    spot = p.free_spot(C2)
    assert spot != c.center
    assert clearance(c.center, pieces) < clearance(spot, pieces) < SPACING
    assert math.dist((spot.x, spot.y), (c.center.x, c.center.y)) <= c.radius_mm


def test_placed_pieces_take_space():
    p = planner()
    targets = [t.target for t in p.recruit(Recruit(C2, 3))]
    for i, t in enumerate(targets):
        assert far_enough(t, targets[:i] + targets[i + 1 :])
        c = p.board[C2]
        assert math.dist((t.x, t.y), (c.center.x, c.center.y)) <= c.radius_mm


def test_taken_place_becomes_free():
    p = planner()
    center = p.board[C1].center
    p.recruit(Recruit(C1, 1))  # кіт у центр
    p.remove(C1, 1)  # забрали в запас
    assert p.free_spot(C1) == center


def test_recruit_comes_from_supply():
    assert planner().recruit(Recruit(C1, 1))[0].source == SUPPLY
