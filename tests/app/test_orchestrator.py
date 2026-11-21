"""Orchestrator з фейковими модулями: перевіряємо порядок дій і реакцію на сигнали."""

from pathlib import Path

import pytest

from rootbot.app.move_planner import MovePlanner
from rootbot.app.operator_ui import OperatorServer
from rootbot.app.orchestrator import Orchestrator
from rootbot.common.types import BoardPoint, ClearingId, Faction, OrderCard, PieceCounts, Suit
from rootbot.config import load_config
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


def obs(pieces, scores=None) -> BoardObservation:
    return BoardObservation(pieces=pieces, scores=scores or {}, confidence=1.0)


def make(observations, **game):
    cfg = load_config()
    cfg["orchestrator"]["tick_period_s"] = 0
    cfg["game"].update(game)
    board = load_board(AUTUMN)
    fakes = Fakes(observations)
    server = OperatorServer()
    o = Orchestrator(
        arm=fakes,
        scanner=fakes,
        detector=fakes,
        board=board,
        planner=MovePlanner(board, [], spacing_mm=30),
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


BEFORE_BATTLE = obs(
    {
        C(1): {BOT: PieceCounts(warriors=5), EYRIE: PieceCounts(warriors=1)},
        C(2): {EYRIE: PieceCounts(warriors=2)},
    }
)
AFTER_BATTLE = obs({C(1): {BOT: PieceCounts(warriors=5)}})
FOX_1 = OrderCard(Suit.FOX, (Suit.FOX,))


def center(o, c):
    return o.board[C(c)].center


def test_full_turn_order():
    o, fakes, server = make([BEFORE_BATTLE, AFTER_BATTLE])
    tick_until(o, waiting(server, "your_turn"))
    server.your_turn()
    tick_until(o, waiting(server, "order_card"))
    assert o.ctx.state.scores[BOT] == 2  # Світанок: панує на 1 з 5 воїнами

    server.order_card(FOX_1)
    tick_until(o, waiting(server, "dice"))
    server.dice_rolled((3, 0))  # бот 3 влучання, Династія 0
    tick_until(o, waiting(server, "your_turn"))

    c1, c2 = center(o, 1), center(o, 2)
    moves = [c for c in fakes.calls if c.startswith("move")]
    assert fakes.calls[:6] == [
        "scan",
        "add",
        "detect",
        "scan [1]",
        "add",
        "detect",
    ]  # + огляд після бою
    assert moves[0] == f"move {c1} -> {c2}"  # рух: 5 - 3 воїни до ворогів; перший — у центр
    assert moves[1].startswith(f"move {c1} -> ") and moves[1] != moves[0]  # другий — поруч
    assert moves[2] == f"move {SUPPLY} -> {c1}"  # набір: 1 на лисячу галявину
    assert fakes.calls[-1] == "home"
    state = o.ctx.state
    assert state.counts(C(1), BOT).warriors == 4 and state.counts(C(2), BOT).warriors == 2
    assert not o.ctx.in_turn


def test_day_restarts_on_card_without_craft_cost():
    o, fakes, server = make([BEFORE_BATTLE, AFTER_BATTLE])
    server.your_turn()
    tick_until(o, waiting(server, "order_card"))
    server.order_card(OrderCard(Suit.MOUSE, ()))  # без вартості крафту, боїв на мишачих немає
    tick_until(
        o, lambda: o.ctx.cards_used == 1 and server.current_state().waiting_for == "order_card"
    )
    server.order_card(OrderCard(Suit.RABBIT, (Suit.RABBIT,)))
    tick_until(o, waiting(server, "your_turn"))
    assert o.ctx.cards_used == 2


def test_failed_step_retries_then_waits_for_human():
    o, fakes, server = make([obs({C(1): {BOT: PieceCounts(warriors=5)}})])
    retries = o.step_retries
    fakes.moves_to_fail = retries + 1  # усі спроби + ще одна
    server.your_turn()
    tick_until(o, waiting(server, "order_card"))
    server.order_card(FOX_1)
    tick_until(o, lambda: server.current_state().step.startswith("needs help"))
    assert server.current_state().error is not None
    for _ in range(3):
        o.tree.tick()  # без людини не продовжує
    assert server.current_state().step.startswith("needs help")

    fakes.moves_to_fail = 0
    server.your_turn()  # людина допомогла
    tick_until(o, lambda: not o.ctx.in_turn)
    c1 = center(o, 1)
    moved = [c for c in fakes.calls if c.startswith(f"move {c1} -> ")]
    assert len(moved) == 2  # жодне переміщення не повторилось двічі


def test_card_given_scores_when_idle():
    o, fakes, server = make([BEFORE_BATTLE])
    server.card_given()
    tick_until(o, lambda: o.ctx.cards_given == 0)
    assert o.ctx.state.scores[BOT] == 1


def test_win_points_end_the_game():
    o, fakes, server = make([obs({}, scores={EYRIE: 30})])
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
