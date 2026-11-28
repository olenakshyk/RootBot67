"""Orchestrator з фейковими модулями: перевіряємо порядок дій і реакцію на сигнали."""

from dataclasses import replace
from pathlib import Path

import pytest

from rootbot.app.move_planner import MovePlanner
from rootbot.app.operator_ui import OperatorServer, WaitingFor
from rootbot.app.orchestrator import Orchestrator
from rootbot.common.types import BoardPoint, ClearingId, Faction, OrderCard, PieceCounts, Suit
from rootbot.config import load_config
from rootbot.game.actions import Move
from rootbot.game.board import load_board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY
from rootbot.vision.observation import BoardObservation, RecheckRequest

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"
C = ClearingId
EYRIE = Faction.EYRIE


class Fakes:
    def __init__(self, observations: list[BoardObservation]) -> None:
        self.calls: list[str] = []
        self.observations = observations
        self.missing: list = []  # що add_frames по черзі каже «ще не видно»; порожньо — видно все
        self.moves_to_fail = 0

    # Arm
    def home(self) -> None:
        self.calls.append("home")

    # Scanner
    def scan(self, request=None):
        self.calls.append("scan" if request is None else f"scan {request.clearings}")
        return ["frame"]

    # Detector
    def _next(self):
        result = self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]
        return result if isinstance(result, tuple) else (result, None)

    def add_frames(self, frames, target=None):
        self.calls.append("add")
        return self.missing.pop(0) if self.missing else None

    def detect(self):
        self.calls.append("detect")
        return self._next()

    def clearings_mask(self, clearings):
        return clearings

    def refine(self, observation, frames):
        self.calls.append("refine")
        return self._next()

    # Manipulator
    def move(self, task) -> None:
        if self.moves_to_fail:
            self.moves_to_fail -= 1
            raise RuntimeError("gripper missed")
        self.calls.append(f"move {task.source} -> {task.target}")


BOARD = load_board(AUTUMN)
BOT_SPACING_MM = 40


def bot_positions(pieces):
    return {
        c: {
            BOT: [
                BoardPoint(BOARD[c].center.x + BOT_SPACING_MM * i, BOARD[c].center.y)
                for i in range(counts[BOT].warriors)
            ]
        }
        for c, counts in pieces.items()
        if BOT in counts
    }


def obs(pieces, scores=None) -> BoardObservation:
    """Огляд усієї дошки: детектор пише кожну видиму галявину, також порожню."""
    return BoardObservation(
        pieces={c: {} for c in BOARD.clearings} | pieces,
        positions=bot_positions(pieces),
        scores=scores or {},
        confidence=1.0,
    )


def make(observations, **game):
    cfg = load_config()
    cfg["orchestrator"]["tick_period_s"] = 0
    cfg["game"].update(game)
    board = load_board(AUTUMN)
    fakes = Fakes(observations)
    server = OperatorServer(die_max=cfg["game"]["die_max"])
    o = Orchestrator(
        arm=fakes,
        scanner=fakes,
        detector=fakes,
        board=board,
        planner=MovePlanner(board, tuple(cfg["board"]["size_mm"]), [], spacing_mm=30),
        manipulator=fakes,
        server=server,
        cfg=cfg,
    )
    return o, fakes, server


def tick_until(o, condition, limit=50):
    for _ in range(limit):
        o.tree.tick()
        if condition():
            return
    raise AssertionError(f"condition not reached, status: {o.server.current_state()}")


def waiting(server, what):
    return lambda: server.current_state().waiting_for == what


def removing(server):
    return lambda: server.current_state().step.startswith("daylight: remove")


BEFORE_BATTLE = obs(
    {
        C(1): {BOT: PieceCounts(warriors=5), EYRIE: PieceCounts(warriors=1)},
        C(2): {EYRIE: PieceCounts(warriors=2)},
    }
)
AFTER_BATTLE = obs({C(1): {BOT: PieceCounts(warriors=5)}, C(2): {EYRIE: PieceCounts(warriors=2)}})
FOX_1 = OrderCard(Suit.FOX, (Suit.FOX,))


def center(o, c):
    return o.board[C(c)].center


def test_full_turn_order():
    o, fakes, server = make([BEFORE_BATTLE, AFTER_BATTLE])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    assert o.ctx.state.scores[BOT] == 2  # Світанок: панує на 1 з 5 воїнами

    server.order_card(FOX_1)
    tick_until(o, waiting(server, WaitingFor.DICE))
    server.dice_rolled((3, 0))  # бот 3 влучання, Династія 0
    tick_until(o, removing(server))
    server.your_turn()
    tick_until(o, lambda: not o.ctx.in_turn)

    c1, c2 = center(o, 1), center(o, 2)
    p0, p1 = AFTER_BATTLE.positions[C(1)][BOT][:2]
    moves = [c for c in fakes.calls if c.startswith("move")]
    assert fakes.calls[:7] == [
        "scan",
        "add",
        "detect",
        "home",
        "scan [1]",
        "add",
        "detect",
    ]  # + огляд після бою
    assert moves[0] == f"move {p0} -> {c2}"  # рух: 5 - 3 воїни до ворогів; перший — у центр
    assert moves[1].startswith(f"move {p1} -> ")
    assert moves[1] != f"move {p1} -> {c2}"  # другий — поруч
    assert moves[2] == f"move {SUPPLY} -> {c1}"  # набір: 1 на лисячу галявину
    assert fakes.calls[-1] == "home"
    state = o.ctx.state
    assert state.counts(C(1), BOT).warriors == 4 and state.counts(C(2), BOT).warriors == 2
    assert not o.ctx.in_turn


def test_day_restarts_on_card_without_craft_cost():
    o, fakes, server = make([BEFORE_BATTLE, AFTER_BATTLE])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    server.order_card(OrderCard(Suit.MOUSE, ()))  # без вартості крафту, боїв на мишачих немає
    tick_until(
        o,
        lambda: (
            o.ctx.cards_used == 1 and server.current_state().waiting_for == WaitingFor.ORDER_CARD
        ),
    )
    server.order_card(OrderCard(Suit.RABBIT, (Suit.RABBIT,)))
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    assert o.ctx.cards_used == 2


def test_failed_step_retries_then_waits_for_human():
    alone = obs({C(1): {BOT: PieceCounts(warriors=5)}})
    o, fakes, server = make([alone])
    retries = o.step_retries
    fakes.moves_to_fail = retries + 1  # усі спроби + ще одна
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    server.order_card(FOX_1)
    tick_until(o, lambda: server.current_state().step.startswith("needs help"))
    assert server.current_state().error is not None
    for _ in range(3):
        o.tree.tick()  # без людини не продовжує
    assert server.current_state().step.startswith("needs help")

    fakes.moves_to_fail = 0
    server.your_turn()  # людина допомогла
    tick_until(o, lambda: not o.ctx.in_turn)
    from_c1 = tuple(f"move {p} -> " for p in alone.positions[C(1)][BOT])
    moved = [c for c in fakes.calls if c.startswith(from_c1)]
    assert len(moved) == 2  # жодне переміщення не повторилось двічі


def start_battle(observations):
    o, fakes, server = make(observations)
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    server.order_card(FOX_1)
    tick_until(o, waiting(server, WaitingFor.DICE))
    return o, fakes, server


def test_battle_losses_wait_for_bot_turn_before_rescan():
    o, fakes, server = start_battle([BEFORE_BATTLE, AFTER_BATTLE])
    server.dice_rolled((3, 0))
    tick_until(o, removing(server))
    for _ in range(3):
        o.tree.tick()

    status = server.current_state()
    assert status.waiting_for == WaitingFor.YOUR_TURN
    assert status.message == f"{EYRIE.value}: remove 1 in clearing 1"
    assert fakes.calls[-1] == "home"
    assert "scan [1]" not in fakes.calls

    server.your_turn()
    tick_until(o, lambda: not o.ctx.in_turn)
    assert fakes.calls[fakes.calls.index("home") + 1] == "scan [1]"


def test_bot_turn_pressed_before_the_wait_does_not_skip_it():
    o, fakes, server = start_battle([BEFORE_BATTLE, AFTER_BATTLE])
    o.ctx.resume = True
    server.dice_rolled((3, 0))
    tick_until(o, removing(server))
    for _ in range(3):
        o.tree.tick()

    assert removing(server)()
    assert "scan [1]" not in fakes.calls


def test_no_battles_no_wait_for_removals():
    alone = obs({C(1): {BOT: PieceCounts(warriors=5)}})
    o, fakes, server = make([alone])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    server.order_card(FOX_1)
    tick_until(o, lambda: not o.ctx.in_turn)
    assert fakes.calls.count("home") == 1


def test_move_onto_unscanned_clearing_scans_it_and_continues():
    o, fakes, server = make([BEFORE_BATTLE, AFTER_BATTLE])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    assert C(2) not in o.planner.occupied
    server.order_card(FOX_1)
    tick_until(o, waiting(server, WaitingFor.DICE))
    server.dice_rolled((3, 0))
    tick_until(o, removing(server))
    server.your_turn()
    tick_until(o, lambda: not o.ctx.in_turn)

    c2 = center(o, 2)
    scan = fakes.calls.index("scan [2]")
    first_move = next(i for i, c in enumerate(fakes.calls) if c.startswith("move"))
    assert scan < first_move
    assert fakes.calls[first_move].endswith(f" -> {c2}")
    assert server.current_state().error is None
    assert not o.ctx.in_turn


def test_clearing_still_unknown_after_scan_waits_for_human():
    no_positions = replace(obs({C(1): {BOT: PieceCounts(warriors=5)}}), positions={})
    o, fakes, server = make([no_positions])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    server.order_card(FOX_1)
    tick_until(o, lambda: server.current_state().step.startswith("needs help"))

    state = server.current_state()
    assert state.waiting_for == WaitingFor.YOUR_TURN
    assert "clearing 1 needs a scan" in state.error
    assert "scan [1]" in fakes.calls
    assert not any(c.startswith("move") for c in fakes.calls)


def test_failed_arm_move_keeps_goal_until_retry_succeeds():
    alone = obs({C(1): {BOT: PieceCounts(warriors=5)}})
    o, fakes, server = make([alone])
    o.planner.process(alone, [C(1), C(2)])
    o.ctx.tasks = o.planner.move(Move(C(1), C(2), 2))
    before = {c: list(points) for c, points in o.planner.bot_positions.items()}
    fakes.moves_to_fail = 1
    with pytest.raises(RuntimeError):
        o.run_tasks()
    assert o.planner.bot_positions == before
    assert o.planner.goals == o.ctx.tasks and len(o.ctx.tasks) == 2

    tasks = list(o.ctx.tasks)
    o.run_tasks()
    assert o.planner.goals == [] and o.ctx.tasks == []
    assert o.planner.bot_positions[C(1)] == alone.positions[C(1)][BOT][2:]
    assert o.planner.bot_positions[C(2)] == [task.target for task in tasks]


def test_aborted_turn_drops_pending_tasks():
    o, fakes, server = make([obs({}, scores={EYRIE: 30})])
    o.planner.score_track = [BoardPoint(0, i) for i in range(31)]
    o.ctx.tasks = o.planner.score(0, 1)
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, lambda: o.ctx.game_over)
    assert o.ctx.tasks == [] and o.planner.goals == []
    assert not any(c.startswith("move") for c in fakes.calls)


def test_card_given_scores_when_idle():
    o, fakes, server = make([BEFORE_BATTLE])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.card_given()
    tick_until(o, lambda: o.ctx.cards_given == 0)
    assert o.ctx.state.scores[BOT] == 1


def test_win_points_end_the_game():
    o, fakes, server = make([obs({}, scores={EYRIE: 30})])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, lambda: o.ctx.game_over)
    assert fakes.calls[-1] == "home"


def test_end_game_signal_stops_run():
    o, fakes, server = make([BEFORE_BATTLE])
    server.end_game()
    o.run()
    assert server.current_state().step == "game over"


@pytest.mark.parametrize("track", [[BoardPoint(0, i) for i in range(31)]])
def test_score_token_is_moved(track):
    o, fakes, server = make([BEFORE_BATTLE])
    o.planner.score_track = track
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.card_given()
    tick_until(o, lambda: o.ctx.cards_given == 0)
    assert fakes.calls == [f"move {track[0]} -> {track[1]}"]


def test_scans_until_covered_then_detects():
    o, fakes, server = make([BEFORE_BATTLE])
    fakes.missing = [RecheckRequest([C(5)], "not visible")]
    assert o.observe_board() is BEFORE_BATTLE
    assert fakes.calls == ["scan", "add", "scan [5]", "add", "detect"]


def test_detector_refines_until_sure():
    unsure = RecheckRequest([C(5)], "disagree")
    o, fakes, server = make([(BEFORE_BATTLE, unsure), (AFTER_BATTLE, unsure), (AFTER_BATTLE, None)])
    assert o.observe_board() is AFTER_BATTLE
    assert fakes.calls == ["scan", "add", "detect", "scan [5]", "refine", "scan [5]", "refine"]


def test_rechecks_share_one_limit():
    o, fakes, server = make([(BEFORE_BATTLE, RecheckRequest([C(5)], "disagree"))])  # завжди ще
    fakes.missing = [RecheckRequest([C(5)], "not visible")] * 2
    assert o.observe_board() is BEFORE_BATTLE
    assert fakes.calls.count("add") - 1 + fakes.calls.count("refine") == o.max_rechecks


def test_board_not_found_scans_the_whole_board_again():
    o, fakes, server = make([BoardObservation(), BEFORE_BATTLE])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, waiting(server, WaitingFor.ORDER_CARD))
    assert fakes.calls[:6] == ["scan", "add", "detect", "scan", "add", "detect"]
    assert o.ctx.state.counts(C(1), BOT).warriors == 5


def test_board_never_found_waits_for_human():
    o, fakes, server = make([BoardObservation()])
    tick_until(o, waiting(server, WaitingFor.YOUR_TURN))
    server.your_turn()
    tick_until(o, lambda: server.current_state().step.startswith("needs help"))

    state = server.current_state()
    assert state.waiting_for == WaitingFor.YOUR_TURN
    assert "board not found on any frame" in state.error


def test_partial_scan_without_requested_clearings_scans_them_again():
    o, fakes, server = make([BoardObservation(pieces={C(1): {}}), BEFORE_BATTLE])
    request = RecheckRequest([C(5)], "needs scan")
    assert o.observe_board(target=[C(5)], request=request) is BEFORE_BATTLE
    assert fakes.calls == ["scan [5]", "add", "detect", "scan [5]", "add", "detect"]
