"""MovePlanner: яких котів брати й куди ставити."""

import math
from pathlib import Path

import pytest

from rootbot.app.move_planner import ClearingNeedsScan, MovePlanner, create_move_planner
from rootbot.common.errors import FatalError
from rootbot.common.types import BoardPoint, ClearingId, Faction
from rootbot.game.actions import Move, Recruit
from rootbot.game.board import load_board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY, PieceMoveTask
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
    p = planner({C1: {BOT: [cat]}, C2: {}})
    assert p.move(Move(C1, C2, 1))[0].source == cat


def test_full_scan_keeps_unseen_clearings():
    old_c1, old_c2, new_c2 = BoardPoint(50, 50), BoardPoint(310, 40), BoardPoint(320, 45)
    p = planner({C1: {BOT: [old_c1]}, C2: {BOT: [old_c2]}})
    p.process(observation({C2: {BOT: [new_c2]}}))
    assert p.remove(C1, 1)[0].source == old_c1
    assert p.remove(C2, 1)[0].source == new_c2


def test_full_scan_keeps_planned_spots_of_unseen_clearings():
    p = planner({C1: {}})
    task = p.recruit(Recruit(C1, 1))[0]
    p.done(task)
    p.process(observation({C2: {}}))
    assert p.remove(C1, 1)[0].source == task.target


def test_take_without_known_position_raises():
    p = planner({C1: {BOT: [BoardPoint(50, 50)]}, C2: {}})
    with pytest.raises(ClearingNeedsScan) as error:
        p.move(Move(C1, C2, 2))
    assert error.value.clearing == C1
    with pytest.raises(ClearingNeedsScan) as error:
        p.remove(C2, 1)
    assert error.value.clearing == C2


def test_put_on_unscanned_clearing_raises():
    p = planner({C1: {BOT: [BoardPoint(50, 50)]}})
    with pytest.raises(ClearingNeedsScan) as error:
        p.recruit(Recruit(C2, 1))
    assert error.value.clearing == C2
    with pytest.raises(ClearingNeedsScan):
        p.free_spot(C2)


def test_move_to_unscanned_clearing_changes_nothing():
    cat = BoardPoint(50, 50)
    p = planner({C1: {BOT: [cat]}})
    with pytest.raises(ClearingNeedsScan) as error:
        p.move(Move(C1, C2, 1))
    assert error.value.clearing == C2
    assert p.bot_positions == {C1: [cat]} and p.occupied == {C1: [cat]} and p.goals == []


def test_planning_keeps_picture_until_done():
    cat = BoardPoint(50, 50)
    p = planner({C1: {BOT: [cat]}, C2: {}})
    tasks = p.move(Move(C1, C2, 1))
    assert p.goals == tasks
    assert p.bot_positions == {C1: [cat], C2: []} and p.occupied == {C1: [cat], C2: []}
    p.done(tasks[0])
    target = tasks[0].target
    assert p.goals == []
    assert p.bot_positions == {C1: [], C2: [target]} and p.occupied == {C1: [], C2: [target]}


def test_pending_source_is_not_taken_again():
    cats = [BoardPoint(50, 50), BoardPoint(90, 50)]
    p = planner({C1: {BOT: cats}, C2: {}})
    first = p.move(Move(C1, C2, 1))[0]
    second = p.remove(C1, 1)[0]
    assert {first.source, second.source} == set(cats)
    with pytest.raises(ClearingNeedsScan):
        p.move(Move(C1, C2, 1))


def test_pending_targets_stay_taken_across_calls():
    p = planner({C2: {}})
    first = p.recruit(Recruit(C2, 1))[0].target
    second = p.recruit(Recruit(C2, 1))[0].target
    assert far_enough(second, [first])


def test_drop_goals_forgets_pending_moves():
    p = planner({C1: {}})
    p.recruit(Recruit(C1, 1))
    p.drop_goals()
    assert p.goals == [] and p.free_spot(C1) == p.board[C1].center


def test_shifted_pending_source_is_matched():
    cat, shifted = BoardPoint(50, 50), BoardPoint(53, 48)
    p = planner({C1: {BOT: [cat]}, C2: {}})
    task = p.move(Move(C1, C2, 1))[0]
    p.process(observation({C1: {BOT: [shifted]}}))
    with pytest.raises(ClearingNeedsScan):
        p.remove(C1, 1)
    p.done(task)
    assert p.bot_positions[C1] == [] and p.occupied[C1] == []
    assert p.bot_positions[C2] == [task.target]


def test_score_goal_is_done():
    track = [[10.0 * i, 400.0] for i in range(31)]
    p = create_move_planner(config(track), load_board(AUTUMN))
    task = p.score(3, 5)[0]
    assert p.goals == [task]
    p.done(task)
    assert p.goals == []


def test_done_with_unknown_task_raises():
    p = planner({C1: {}})
    with pytest.raises(ValueError):
        p.done(PieceMoveTask(SUPPLY, p.board[C1].center))


def test_clearing_seen_empty_is_known():
    p = planner({C2: {}})
    assert len(p.recruit(Recruit(C2, 1))) == 1


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
    p = planner({C2: {}})
    targets = [t.target for t in p.recruit(Recruit(C2, 3))]
    for i, t in enumerate(targets):
        assert far_enough(t, targets[:i] + targets[i + 1 :])
        c = p.board[C2]
        assert math.dist((t.x, t.y), (c.center.x, c.center.y)) <= c.radius_mm


def test_taken_place_becomes_free():
    p = planner({C1: {}})
    center = p.board[C1].center
    p.done(p.recruit(Recruit(C1, 1))[0])  # кіт у центр
    p.done(p.remove(C1, 1)[0])  # забрали в запас
    assert p.free_spot(C1) == center


def test_recruit_comes_from_supply():
    assert planner({C1: {}}).recruit(Recruit(C1, 1))[0].source == SUPPLY


def config(score_track):
    board = {"size_mm": list(BOARD_MM), "piece_spacing_mm": SPACING, "score_track_mm": score_track}
    return {"board": board, "game": {"win_points": 30}}


@pytest.mark.parametrize("score_track", [[], None])
def test_no_score_track_leaves_token_in_place(score_track):
    p = create_move_planner(config(score_track), load_board(AUTUMN))
    assert p.score(3, 5) == []


def test_full_score_track_moves_token():
    track = [[10.0 * i, 400.0] for i in range(31)]
    p = create_move_planner(config(track), load_board(AUTUMN))
    task = p.score(3, 5)[0]
    assert (task.source, task.target) == (BoardPoint(30, 400), BoardPoint(50, 400))


def test_short_score_track_is_fatal():
    track = [[10.0 * i, 400.0] for i in range(20)]
    with pytest.raises(FatalError):
        create_move_planner(config(track), load_board(AUTUMN))
