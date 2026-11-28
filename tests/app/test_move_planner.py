"""MovePlanner: яких котів брати й куди ставити."""

import math
from pathlib import Path

from rootbot.app.move_planner import MovePlanner
from rootbot.common.types import BoardPoint, ClearingId, Faction
from rootbot.game.actions import Move, Recruit
from rootbot.game.board import load_board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY
from rootbot.vision.observation import BoardObservation

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"
C1, C2 = ClearingId(1), ClearingId(2)
SPACING = 30


def planner(positions=None):
    p = MovePlanner(load_board(AUTUMN), [], spacing_mm=SPACING)
    p.process(BoardObservation(positions=positions or {}))
    return p


def far_enough(point, others):
    return all(math.dist((point.x, point.y), (q.x, q.y)) >= SPACING for q in others)


def test_takes_known_bot_positions_first():
    cat = BoardPoint(100, 100)
    p = planner({C1: {BOT: [cat]}})
    assert p.move(Move(C1, C2, 1))[0].source == cat


def test_free_spot_avoids_other_pieces():
    p = planner()
    center = p.board[C2].center
    p.process(BoardObservation(positions={C2: {Faction.EYRIE: [center]}}))
    spot = p.free_spot(C2)
    assert spot != center and far_enough(spot, [center])


def clearance(point, others):
    return min(math.dist((point.x, point.y), (q.x, q.y)) for q in others)


def test_packed_clearing_gives_least_crowded_spot():
    p = planner()
    c = p.board[C2]
    step = SPACING * 2 / 3
    n = math.ceil((c.radius_mm + SPACING) / step)
    pieces = [
        BoardPoint(c.center.x + i * step, c.center.y + j * step)
        for i in range(-n, n + 1)
        for j in range(-n, n + 1)
    ]
    p.process(BoardObservation(positions={C2: {Faction.EYRIE: pieces}}))
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
