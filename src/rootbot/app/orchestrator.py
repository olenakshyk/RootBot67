"""Orchestrator — веде партію: єдиний, хто знає про vision, game і robot.

Партія — Behaviour Tree (py_trees): build_tree() збирає дерево, run() тикає його до кінця гри.
Схема дерева — docs/decisions/0007-behaviour-tree.md (вручну: змінюєш build_tree() — онови її);
вимоги — docs/requirements/orchestrator.md.

Кроки не повторюють уже зроблене: дії плануються раз (TurnContext.done), дія знімається з черги
TurnContext.actions лише після планування, задача руки з черги TurnContext.tasks — лише після
успішного переміщення.

Галявину, якої не знає MovePlanner (ClearingNeedsScan), крок оглядає й повторюється (Step.update):
спершу виконує вже сплановані переміщення, потім частковий огляд цієї галявини, як після боїв.
Не знає її й після огляду — звичайний невдалий крок: повтори, потім людина (RetryOrAskHuman).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import py_trees
from py_trees.common import Status

from rootbot.app.move_planner import ClearingNeedsScan, MovePlanner
from rootbot.app.operator_ui import (
    CardGiven,
    DiceRolled,
    EndGame,
    OperatorServer,
    OrderCardRevealed,
    WaitingFor,
    YourTurn,
)
from rootbot.common.errors import FatalError
from rootbot.common.types import ClearingId, Faction, OrderCard
from rootbot.game import marquise_bot as bot
from rootbot.game.actions import Battle, Move, Recruit
from rootbot.game.board import Board
from rootbot.game.state import BOT, GameState
from rootbot.robot.arm import Arm
from rootbot.robot.manipulator import Manipulator, PieceMoveTask
from rootbot.robot.scan import Scanner
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.detector import Detector
from rootbot.vision.observation import BoardObservation, RecheckRequest

log = logging.getLogger(__name__)


@dataclass
class TurnContext:
    """Спільні дані дерева: стан партії, що прийшло від людей і що вже зроблено в цьому ході."""

    state: GameState  # один на всю партію, щоходу оновлюється з камери (GameState.update)
    # сигнали
    turn_requested: bool = False
    resume: bool = False  # «Хід бота» під час ходу: людина допомогла, продовжити
    game_over: bool = False
    cards_given: int = 0
    order_cards: list[OrderCard] = field(default_factory=list)
    dice: list[DiceRolled] = field(default_factory=list)
    # хід
    in_turn: bool = False
    card: OrderCard | None = None
    cards_used: int = 0  # скільки карт Розкладу відкрито в цьому ході
    done: set[str] = field(default_factory=set)  # кроки, дії яких уже пораховано
    actions: list[Move | Recruit] = field(default_factory=list)
    tasks: list[PieceMoveTask] = field(default_factory=list)  # ще не виконані переміщення
    battles: list[Battle] = field(default_factory=list)
    battle_index: int = 0
    restart_day: bool = False  # карта без вартості крафту (recruits -> None): День ще раз
    error: str | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        arm: Arm,
        scanner: Scanner,
        detector: Detector,
        board: Board,
        planner: MovePlanner,
        manipulator: Manipulator,
        server: OperatorServer,
        cfg: dict,
    ) -> None:
        self.arm = arm
        self.scanner = scanner
        self.detector = detector
        self.board = board
        self.game: dict = cfg["game"]  # параметри бота: game.birdsong, game.move, ...
        self.planner = planner
        self.manipulator = manipulator
        self.server = server
        self.tick_period_s: float = cfg["orchestrator"]["tick_period_s"]
        self.step_retries: int = cfg["orchestrator"]["step_retries"]
        self.max_rechecks: int = cfg["camera"]["max_rechecks"]
        self.win_points: int = cfg["game"]["win_points"]
        self.schedule_size: int = cfg["game"]["evening"]["schedule_size"]
        self.ctx = TurnContext(state=GameState(board))
        self.tree = py_trees.trees.BehaviourTree(self.build_tree())

    def run(self) -> None:
        """Партія: тикати дерево, поки не кінець гри. Ctrl+C іде нагору в main."""
        log.debug(f"behaviour tree:\n{py_trees.display.unicode_tree(self.tree.root)}")
        log.info("Orchestrator: started, waiting for signals")
        tip = None
        while not self.ctx.game_over:
            self.tree.tick()
            # debug: дерево зі статусами, але лише коли змінився активний вузол (не щотику)
            if log.isEnabledFor(logging.DEBUG) and self.tree.tip() is not tip:
                tip = self.tree.tip()
                shown = py_trees.display.unicode_tree(self.tree.root, show_status=True)
                log.debug(f"tree:\n{shown}")
            time.sleep(self.tick_period_s)
        self.tree.root.stop(Status.INVALID)
        self.drop_tasks()
        self.status(step="game over", waiting_for=None)
        log.info(f"Orchestrator: game over, scores {self._scores()}")

    def build_tree(self) -> py_trees.behaviour.Behaviour:
        def retry(step: Step) -> RetryOrAskHuman:
            return RetryOrAskHuman(self, step)

        day = py_trees.composites.Sequence(
            "day",
            memory=True,
            children=[
                WaitOrderCard(self),
                retry(Battles(self)),
                retry(Rescan(self)),
                retry(Moves(self)),
                retry(Recruits(self)),
            ],
        )
        steps = py_trees.composites.Sequence(
            "steps",
            memory=True,
            children=[
                StartTurn(self),
                retry(ScanBoard(self)),
                NobodyWon(self),
                retry(Birdsong(self)),
                RepeatDay(self, day),
                Evening(self),
                FinishTurn(self),
            ],
        )
        turn = py_trees.composites.Selector("turn", memory=False, children=[steps, AbortTurn(self)])
        bot_turn = py_trees.composites.Sequence(
            "bot turn",
            memory=False,
            children=[Check("TurnRequested?", lambda: self.ctx.turn_requested), turn],
        )
        card_given = py_trees.composites.Sequence(
            "card given",
            memory=False,
            children=[
                Check("CardGivenPending?", lambda: self.ctx.cards_given > 0),
                ScoreCardGiven(self),
            ],
        )
        what_now = py_trees.composites.Selector(
            "what now", memory=False, children=[bot_turn, card_given, Idle(self)]
        )
        return py_trees.composites.Sequence(
            "root", memory=False, children=[ReadSignals(self), what_now]
        )

    # --- спільне для кроків ---

    def status(self, **changes: object) -> None:
        self.server.set_status(scores=self._scores(), **changes)

    def observe_board(
        self, target: BoardMask | None = None, request: RecheckRequest | None = None
    ) -> BoardObservation:
        """Огляд `target` (за замовчуванням уся дошка), спільний ліміт camera.max_rechecks:
        1. знімати, поки детектор не скаже, що бачить усю ціль (add_frames -> None);
        2. розпізнати (detect);
        3. уточнювати, поки детектору є що переглянути (refine).
        Повертає найкраще, що є."""
        missing = self.detector.add_frames(self.scanner.scan(request), target=target)
        rechecks = 0
        while missing and rechecks < self.max_rechecks:
            rechecks += 1
            missing = self.detector.add_frames(self.scanner.scan(missing))
        obs, recheck = self.detector.detect()
        while recheck and rechecks < self.max_rechecks:
            rechecks += 1
            obs, recheck = self.detector.refine(obs, self.scanner.scan(recheck))
        if recheck:
            log.warning(f"still unsure after {rechecks} rechecks: {recheck}")
        return obs

    def scan_clearings(self, clearings: list[ClearingId], reason: str) -> None:
        """Частковий огляд лише `clearings`: оновлює на них GameState і MovePlanner. Keep — як
        бачить огляд."""
        obs = self.observe_board(
            target=self.detector.clearings_mask(clearings),
            request=RecheckRequest(clearings, reason),
        )
        self.ctx.state.update(
            pieces=obs.pieces,
            keep_clearing=obs.keep_clearing,
            scores=obs.scores,
            clearings=clearings,
        )
        self.planner.process(obs, clearings)

    def wait_for(self, what: WaitingFor, step: str, **changes: object) -> None:
        """Статус «чекаю людину»: що саме потрібно (what) і на якому кроці. Лише якщо змінилося."""
        now = self.server.current_state()
        if now.waiting_for != what or now.step != step:
            self.status(step=step, waiting_for=what, **changes)

    def add_score(self, points: int, reason: str) -> None:
        scores = self.ctx.state.scores
        old = scores.get(BOT, 0)
        scores[BOT] = old + points
        log.info(f"score +{points} ({reason}): {old} -> {scores[BOT]}")
        self.ctx.tasks += self.planner.score(old, scores[BOT])

    def run_tasks(self) -> None:
        """Виконати заплановані переміщення; лише після успіху задача знімається з черги, а
        MovePlanner оновлює картину дошки (done)."""
        while self.ctx.tasks:
            task = self.ctx.tasks[0]
            self.manipulator.move(task)
            self.planner.done(task)
            self.ctx.tasks.pop(0)

    def drop_tasks(self) -> None:
        """Забути невиконані переміщення: черга ctx.tasks і цілі MovePlanner разом."""
        self.ctx.tasks.clear()
        self.planner.drop_goals()

    def card(self) -> OrderCard:
        if self.ctx.card is None:
            raise RuntimeError("no order card")
        return self.ctx.card

    def _scores(self) -> dict[Faction, int]:
        return dict(self.ctx.state.scores)


# --- вузли дерева ---


class Step(py_trees.behaviour.Behaviour):
    """Крок ходу: step() повертає статус; виняток (крім FatalError) — FAILURE, помилка в лозі."""

    def __init__(self, o: Orchestrator, name: str | None = None) -> None:
        super().__init__(name or type(self).__name__)
        self.o = o

    def update(self) -> Status:
        try:
            return self.step_scanning_unknown_clearing()
        except FatalError:
            raise
        except Exception as e:
            log.exception(f"{self.name}: failed")
            self.o.ctx.error = f"{self.name}: {e}"
            return Status.FAILURE

    def step_scanning_unknown_clearing(self) -> Status:
        """step(); галявину, якої не знає MovePlanner, — оглянути й повторити step() раз."""
        try:
            return self.step()
        except ClearingNeedsScan as e:
            log.info(f"{self.name}: {e}, scanning it")
            self.o.run_tasks()
            self.o.scan_clearings([e.clearing], "needs scan")
            return self.step()

    def step(self) -> Status:
        raise NotImplementedError


class Check(py_trees.behaviour.Behaviour):
    """Умова: SUCCESS, якщо predicate() істинний, інакше FAILURE."""

    def __init__(self, name: str, predicate: Callable[[], bool]) -> None:
        super().__init__(name)
        self.predicate = predicate

    def update(self) -> Status:
        return Status.SUCCESS if self.predicate() else Status.FAILURE


class ReadSignals(Step):
    def step(self) -> Status:
        ctx = self.o.ctx
        while (signal := self.o.server.next_signal()) is not None:
            log.info(f"signal: {signal}")
            match signal:
                case YourTurn():
                    if ctx.in_turn:
                        ctx.resume = True
                    else:
                        ctx.turn_requested = True
                case EndGame():
                    ctx.game_over = True
                case CardGiven():
                    ctx.cards_given += 1
                case OrderCardRevealed(card=card):
                    ctx.order_cards.append(card)
                case DiceRolled():
                    ctx.dice.append(signal)
        return Status.SUCCESS


class Idle(Step):
    def step(self) -> Status:
        self.o.wait_for(WaitingFor.YOUR_TURN, "idle")
        return Status.RUNNING


class ScoreCardGiven(Step):
    def step(self) -> Status:
        if "card_given" not in self.o.ctx.done:
            score = bot.card_given(points=self.o.game["traits"]["card_given_points"])
            self.o.add_score(score.points, score.reason)
            self.o.ctx.done.add("card_given")
        self.o.run_tasks()
        self.o.ctx.done.discard("card_given")
        self.o.ctx.cards_given -= 1
        return Status.SUCCESS


class StartTurn(Step):
    def step(self) -> Status:
        ctx = self.o.ctx
        ctx.in_turn, ctx.resume, ctx.error = True, False, None
        ctx.cards_used, ctx.card = 0, None
        ctx.done.clear()
        log.info("=== bot turn ===")
        self.o.status(step="birdsong: scanning the board", waiting_for=None, message="", error=None)
        return Status.SUCCESS


class ScanBoard(Step):
    def step(self) -> Status:
        obs = self.o.observe_board()
        self.o.ctx.state.update(
            pieces=obs.pieces, keep_clearing=obs.keep_clearing, scores=obs.scores
        )
        self.o.planner.process(obs)
        return Status.SUCCESS


class NobodyWon(Step):
    def step(self) -> Status:
        winners = [f.value for f, s in self.o.ctx.state.scores.items() if s >= self.o.win_points]
        if not winners:
            return Status.SUCCESS
        log.info(f"{', '.join(winners)} reached {self.o.win_points} VP")
        self.o.ctx.game_over = True
        return Status.FAILURE


class Birdsong(Step):
    def step(self) -> Status:
        if "birdsong" not in self.o.ctx.done:
            score = bot.birdsong(self.o.ctx.state, **self.o.game["birdsong"])
            self.o.add_score(score.points, score.reason)
            self.o.ctx.done.add("birdsong")
        self.o.run_tasks()
        return Status.SUCCESS


class WaitOrderCard(Step):
    def step(self) -> Status:
        ctx = self.o.ctx
        if not ctx.order_cards:
            self.o.wait_for(WaitingFor.ORDER_CARD, "daylight: reveal the left order card")
            return Status.RUNNING
        ctx.card = ctx.order_cards.pop(0)
        ctx.cards_used += 1
        ctx.done -= {"battles", "moves", "recruits"}
        ctx.restart_day = False
        log.info(
            f"order card: {ctx.card.suit.value}, craft {[s.value for s in ctx.card.craft_cost]}"
        )
        self.o.status(step="daylight", waiting_for=None)
        return Status.SUCCESS


class Battles(Step):
    def step(self) -> Status:
        ctx, o = self.o.ctx, self.o
        state = o.ctx.state
        if "battles" not in ctx.done:
            ctx.battles, ctx.battle_index = bot.battles(state, o.card()), 0
            ctx.done.add("battles")
        o.run_tasks()
        while ctx.battle_index < len(ctx.battles):
            battle = ctx.battles[ctx.battle_index]
            if not ctx.dice:
                where = f"battle in clearing {battle.clearing} vs {battle.defender.value}"
                o.wait_for(WaitingFor.DICE, f"{where}: roll the dice")
                return Status.RUNNING
            dice = ctx.dice[0]
            result = bot.resolve_battle(
                state, battle, dice.rolls, vagabond_swords=dice.vagabond_swords
            )
            ctx.tasks += o.planner.remove(battle.clearing, result.bot_warriors_lost)
            ctx.dice.pop(0)
            log.info(f"battle in clearing {battle.clearing}: {result}")
            state.add_warriors(battle.clearing, -result.bot_warriors_lost)
            hits = result.defender_hits
            message = f"{battle.defender.value}: remove {hits} in clearing {battle.clearing}"
            if result.keep_lost:
                state.keep_clearing = None
                message += "; remove the Keep"  # тимчасово: AUT-8
            o.status(step="daylight: battle", waiting_for=None, message=message)
            ctx.battle_index += 1
            o.run_tasks()
        return Status.SUCCESS


class Rescan(Step):
    """Після боїв: оглянути лише галявини боїв, бо втрати людей прибирають люди."""

    def step(self) -> Status:
        clearings = sorted({b.clearing for b in self.o.ctx.battles})
        if not clearings:
            return Status.SUCCESS
        self.o.scan_clearings(clearings, "after battles")
        return Status.SUCCESS


class Moves(Step):
    def step(self) -> Status:
        o, state = self.o, self.o.ctx.state
        if "moves" not in o.ctx.done:
            o.ctx.actions = list(bot.moves(state, o.card(), **o.game["move"]))
            o.ctx.done.add("moves")
        while o.ctx.actions:
            move = o.ctx.actions[0]
            log.info(f"{move}")
            o.ctx.tasks += o.planner.move(move)
            state.add_warriors(move.src, -move.count)
            state.add_warriors(move.dst, move.count)
            o.ctx.actions.pop(0)
        o.status(step="daylight: move", waiting_for=None)
        o.run_tasks()
        return Status.SUCCESS


class Recruits(Step):
    def step(self) -> Status:
        o, state = self.o, self.o.ctx.state
        if "recruits" not in o.ctx.done:
            recruits = bot.recruits(state, o.card(), **o.game["recruit"])
            o.ctx.restart_day = recruits is None  # карта без вартості крафту
            o.ctx.actions = list(recruits or [])
            o.ctx.done.add("recruits")
        while o.ctx.actions:
            recruit = o.ctx.actions[0]
            log.info(f"{recruit}")
            o.ctx.tasks += o.planner.recruit(recruit)
            state.add_warriors(recruit.clearing, recruit.count)
            o.ctx.actions.pop(0)
        o.status(step="daylight: recruit", waiting_for=None)
        o.run_tasks()
        return Status.SUCCESS


class Evening(Step):
    def step(self) -> Status:
        in_schedule = max(self.o.schedule_size - self.o.ctx.cards_used, 0)
        draw = bot.evening(in_schedule, **self.o.game["evening"])
        log.info(f"evening: draw {draw} order cards")  # добирають люди: AUT-9
        self.o.status(step="evening", message=f"draw {draw} order cards into the Schedule")
        return Status.SUCCESS


class FinishTurn(Step):
    def step(self) -> Status:
        self.o.arm.home()
        self.o.ctx.in_turn = self.o.ctx.turn_requested = False
        log.info(f"=== bot turn done, score {self.o.ctx.state.scores.get(BOT, 0)} ===")
        self.o.status(step="idle", waiting_for=WaitingFor.YOUR_TURN)
        return Status.SUCCESS


class AbortTurn(Step):
    """Хід не вдався без повтору (кінець гри): рука в home, невиконані переміщення забуто, хід
    завершено."""

    def step(self) -> Status:
        self.o.arm.home()
        self.o.drop_tasks()
        self.o.ctx.in_turn = self.o.ctx.turn_requested = False
        if not self.o.ctx.game_over:
            log.error(f"bot turn aborted: {self.o.ctx.error}")
            self.o.status(
                step="turn aborted", waiting_for=WaitingFor.YOUR_TURN, error=self.o.ctx.error
            )
        return Status.SUCCESS


class RepeatDay(py_trees.decorators.Decorator):
    """День ще раз з наступною картою, якщо карта без вартості крафту і в Розкладі ще є карти."""

    def __init__(self, o: Orchestrator, child: py_trees.behaviour.Behaviour) -> None:
        super().__init__(name="repeat day, if card has no craft cost", child=child)
        self.o = o

    def update(self) -> Status:
        if self.decorated.status != Status.SUCCESS:
            return self.decorated.status
        if self.o.ctx.restart_day and self.o.ctx.cards_used < self.o.schedule_size:
            log.info("order card without craft cost: daylight again with the next card")
            self.decorated.stop(Status.INVALID)
            return Status.RUNNING
        return Status.SUCCESS


class RetryOrAskHuman(py_trees.decorators.Decorator):
    """Невдалий крок: ще step_retries спроб, потім рука в home і чекати «Хід бота» від людини."""

    def __init__(self, o: Orchestrator, child: Step) -> None:
        super().__init__(name=f"retry ×{o.step_retries}, then ask human", child=child)
        self.o = o
        self.failures = 0
        self.waiting = False

    def tick(self) -> Iterator[py_trees.behaviour.Behaviour]:
        if self.waiting:
            if not self.o.ctx.resume:
                self.status = Status.RUNNING
                yield self
                return
            log.info(f"{self.decorated.name}: resumed by the operator")
            self.o.ctx.resume, self.waiting, self.failures = False, False, 0
            self.o.status(waiting_for=None, error=None)
        yield from super().tick()

    def update(self) -> Status:
        if self.decorated.status != Status.FAILURE:
            return self.decorated.status
        self.failures += 1
        if self.failures <= self.o.step_retries:
            log.warning(f"{self.decorated.name}: 🟡 retry {self.failures}/{self.o.step_retries}")
            return Status.RUNNING
        log.error(f"{self.decorated.name}: 🔴 needs help, waiting for «bot turn» to continue")
        self.o.arm.home()
        self.o.ctx.resume, self.waiting = False, True
        self.o.wait_for(
            WaitingFor.YOUR_TURN, f"needs help: {self.decorated.name}", error=self.o.ctx.error
        )
        return Status.RUNNING

    def terminate(self, new_status: Status) -> None:
        if new_status != Status.RUNNING:
            self.failures, self.waiting = 0, False
