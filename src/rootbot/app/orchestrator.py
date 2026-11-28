"""Orchestrator — веде партію: єдиний, хто знає про vision, game і robot.

Партія — Behaviour Tree (py_trees): build_tree() збирає дерево, run() тикає його до кінця гри.
Схема дерева — docs/decisions/0007-behaviour-tree.md (вручну: змінюєш build_tree() — онови її);
вимоги — docs/requirements/orchestrator.md.

Кроки не повторюють уже зроблене: дії плануються раз (TurnContext.done), дія знімається з черги
TurnContext.actions лише після планування.

Дія бота, що рухає фігурки й змінює дані, атомарна (ArmAction): спершу рука виконує всі її
переміщення (переміщення знімається з дії лише після успіху), лише потім змінюються дані —
воїни в GameState, ПО бота. Невдала дія даних не змінює, повтор кроку продовжує ту саму дію, тож
стан і дошка не розходяться, а ПО не рахуються двічі. Кроки виконують кожну дію, перш ніж
планувати наступну.

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
    ANSWERS,
    CardGiven,
    DiceRolled,
    EndGame,
    OperatorServer,
    OrderCardRevealed,
    WaitingFor,
    YourTurn,
)
from rootbot.common.errors import FatalError, TemporaryError
from rootbot.common.types import ClearingId, Faction, OrderCard
from rootbot.game import marquise_bot as bot
from rootbot.game.actions import Battle, BattleResult, Move, Recruit
from rootbot.game.board import Board
from rootbot.game.state import BOT, GameState
from rootbot.robot.arm import Arm
from rootbot.robot.manipulator import Manipulator, PieceMoveTask
from rootbot.robot.scan import Scanner
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.detector import Detector
from rootbot.vision.observation import BoardObservation, RecheckRequest

log = logging.getLogger(__name__)


class GameOver(Exception):
    """Дія закінчила гру (бот набрав game.win_points ПО): крок зупиняється, хід перервано."""


@dataclass
class ArmAction:
    """Дія бота: переміщення руки (tasks) і зміна даних (apply), яка настає лише після них усіх."""

    tasks: list[PieceMoveTask]
    apply: Callable[[], None]


@dataclass
class TurnContext:
    """Спільні дані дерева: стан партії, що прийшло від людей і що вже зроблено в цьому ході."""

    state: GameState  # один на всю партію, щоходу оновлюється з камери (GameState.update)
    # сигнали
    turn_requested: bool = False
    resume: bool = False  # «Хід бота» під час ходу: людина допомогла, продовжити
    waiting_for: WaitingFor | None = None  # запит до людини, відповідь на який ще не прочитано
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
    arm_actions: list[ArmAction] = field(default_factory=list)  # ще не завершені дії руки
    battles: list[Battle] = field(default_factory=list)
    battle_index: int = 0
    removals: list[str] = field(default_factory=list)
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
        self.card_given_delay_s: float = cfg["orchestrator"]["card_given_delay_s"]
        self.max_rechecks: int = cfg["camera"]["max_rechecks"]
        self.win_points: int = cfg["game"]["win_points"]
        self.schedule_size: int = cfg["game"]["evening"]["schedule_size"]
        dice = cfg["game"]["dice"]
        if dice == "automated":
            raise FatalError(
                "game.dice: 'automated' (the robot throws real dice) is planned; use 'manual'"
            )
        if dice != "manual":
            raise FatalError(f"game.dice: unknown value {dice!r}; use 'manual'")
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
        self.drop_arm_actions()
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
                retry(WaitLossesRemoved(self)),
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
                retry(Evening(self)),
                retry(FinishTurn(self)),
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
                Check(
                    "CardGivenPending?",
                    lambda: self.ctx.cards_given > 0 or bool(self.ctx.arm_actions),
                ),
                retry(ScoreCardGiven(self)),
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
        if "waiting_for" in changes and changes["waiting_for"] is None:
            self.ctx.waiting_for = None
        self.server.set_status(scores=self._scores(), **changes)

    def observe_board(
        self, target: BoardMask | None = None, request: RecheckRequest | None = None
    ) -> BoardObservation:
        """Огляд `target` (за замовчуванням уся дошка), спільний ліміт camera.max_rechecks:
        1. знімати, поки детектор не скаже, що бачить усю ціль (add_frames -> None);
        2. розпізнати (detect); огляд, що не покрив жодної галявини (з request.clearings, якщо
           задано), — знову з кроку 1 для всієї цілі, а після ліміту — TemporaryError;
        3. уточнювати, поки детектору є що переглянути (refine).
        Повертає найкраще, що є."""
        rechecks = 0
        while True:
            missing = self.detector.add_frames(self.scanner.scan(request), target=target)
            while missing and rechecks < self.max_rechecks:
                rechecks += 1
                missing = self.detector.add_frames(self.scanner.scan(missing))
            obs, recheck = self.detector.detect()
            if self._covers_any_clearing(obs, request):
                break
            if rechecks >= self.max_rechecks:
                raise TemporaryError(self._nothing_seen(request))
            rechecks += 1
            log.warning(f"{self._nothing_seen(request)}: scanning again")
        while recheck and rechecks < self.max_rechecks:
            rechecks += 1
            obs, recheck = self.detector.refine(obs, self.scanner.scan(recheck))
        if recheck:
            log.warning(f"still unsure after {rechecks} rechecks: {recheck}")
        return obs

    @staticmethod
    def _covers_any_clearing(obs: BoardObservation, request: RecheckRequest | None) -> bool:
        """Огляд побачив хоч одну галявину: будь-яку, а з request — хоч одну з request.clearings."""
        if request is None:
            return bool(obs.pieces)
        return any(c in obs.pieces for c in request.clearings)

    @staticmethod
    def _nothing_seen(request: RecheckRequest | None) -> str:
        if request is None:
            return "board not found on any frame"
        return f"scan covers none of the requested clearings {request.clearings}"

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
        """Статус «чекаю людину»: що саме потрібно (what) і на якому кроці. Сервер чекає what раз
        на запит: прийнявши відповідь, він сам скидає waiting_for, і поки ReadSignals її не
        прочитав (ctx.waiting_for), запит не взводиться вдруге — повторне натискання сервер
        відхиляє. Той самий запит на іншому кроці лише змінює підпис кроку."""
        if self.ctx.waiting_for == what:
            if self.server.current_state().step != step:
                self.status(step=step, **changes)
            return
        self.ctx.waiting_for = what
        self.status(step=step, waiting_for=what, **changes)

    def score_action(self, points: int, reason: str) -> ArmAction:
        """Жетон рахунку бота на points клітинок далі, потім +points ПО. Бот набрав
        game.win_points ПО — кінець гри одразу (ctx.game_over)."""
        scores = self.ctx.state.scores
        old = scores.get(BOT, 0)

        def apply() -> None:
            scores[BOT] = scores.get(BOT, 0) + points
            log.info(f"score +{points} ({reason}): {scores[BOT]}")
            if scores[BOT] >= self.win_points:
                log.info(f"bot reached {scores[BOT]} VP: game over")
                self.ctx.game_over = True
                self.status(message=f"bot reached {scores[BOT]} VP: game over")

        return ArmAction(self.planner.score(old, old + points), apply)

    def move_action(self, move: Move) -> ArmAction:
        """Коти бота з move.src на move.dst, потім воїни в GameState."""
        state = self.ctx.state

        def apply() -> None:
            state.add_warriors(move.src, -move.count)
            state.add_warriors(move.dst, move.count)

        return ArmAction(self.planner.move(move), apply)

    def recruit_action(self, recruit: Recruit) -> ArmAction:
        """Коти бота із запасу на recruit.clearing, потім воїни в GameState."""
        state = self.ctx.state

        def apply() -> None:
            state.add_warriors(recruit.clearing, recruit.count)

        return ArmAction(self.planner.recruit(recruit), apply)

    def losses_action(self, battle: Battle, result: BattleResult) -> ArmAction:
        """Втрати бота в бою — у запас, потім воїни (і Keep, якщо знятий) у GameState."""
        state = self.ctx.state

        def apply() -> None:
            state.add_warriors(battle.clearing, -result.bot_warriors_lost)
            if result.keep_lost:
                state.keep_clearing = None

        return ArmAction(self.planner.remove(battle.clearing, result.bot_warriors_lost), apply)

    def run_arm_actions(self) -> None:
        """Виконати дії з черги по черзі. Переміщення знімається з дії лише після успіху, тоді ж
        MovePlanner оновлює картину дошки (done); коли всі переміщення дії вдалися — зміна даних
        (apply), і дія знімається з черги. Дія закінчила гру — GameOver."""
        while self.ctx.arm_actions:
            action = self.ctx.arm_actions[0]
            while action.tasks:
                task = action.tasks[0]
                self.manipulator.move(task)
                self.planner.done(task)
                action.tasks.pop(0)
            action.apply()
            self.ctx.arm_actions.pop(0)
            if self.ctx.game_over:
                raise GameOver()

    def home_or_report(self) -> None:
        """Рука в home; збій руки програму не зупиняє: він у лозі й у статусі (error)."""
        try:
            self.arm.home()
        except Exception as e:
            log.exception("arm home failed")
            failure = f"arm home failed: {e}"
            self.ctx.error = failure if self.ctx.error is None else f"{self.ctx.error}; {failure}"
            self.status(error=self.ctx.error)

    def drop_arm_actions(self) -> None:
        """Забути незавершені дії разом з їхніми змінами даних і цілями MovePlanner."""
        self.ctx.arm_actions.clear()
        self.planner.drop_goals()

    def card(self) -> OrderCard:
        if self.ctx.card is None:
            raise RuntimeError("no order card")
        return self.ctx.card

    def _scores(self) -> dict[Faction, int]:
        return dict(self.ctx.state.scores)


# --- вузли дерева ---


class Step(py_trees.behaviour.Behaviour):
    """Крок ходу: step() повертає статус; виняток (крім FatalError) — FAILURE, помилка в лозі.
    GameOver — FAILURE без помилки: хід переривається (AbortTurn), як після NobodyWon."""

    def __init__(self, o: Orchestrator, name: str | None = None) -> None:
        super().__init__(name or type(self).__name__)
        self.o = o

    def update(self) -> Status:
        try:
            return self.step_scanning_unknown_clearing()
        except GameOver:
            return Status.FAILURE
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
            self.o.run_arm_actions()
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
            if isinstance(signal, ANSWERS):
                ctx.waiting_for = None
            match signal:
                case YourTurn():
                    if ctx.in_turn or ctx.arm_actions:
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
    """«Карту дано» між ходами: через orchestrator.card_given_delay_s рука пересуває жетон рахунку,
    лише потім +ПО. Без доріжки рахунку переміщень немає — ПО одразу. Карта стає дією один раз
    (cards_given -1), тож повтор продовжує ту саму дію."""

    def initialise(self) -> None:
        self.due: float | None = None

    def step(self) -> Status:
        o, ctx = self.o, self.o.ctx
        if not ctx.arm_actions:
            if self.due is None:
                delay = o.card_given_delay_s if o.planner.score_track else 0.0
                self.due = time.monotonic() + delay
                if delay:
                    o.status(message=f"card given: the arm moves the score token in {delay:g} s")
            if time.monotonic() < self.due:
                return Status.RUNNING
            score = bot.card_given(points=o.game["traits"]["card_given_points"])
            ctx.arm_actions.append(o.score_action(score.points, score.reason))
            ctx.cards_given -= 1
        o.run_arm_actions()
        o.status(message="")
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
            self.o.ctx.arm_actions.append(self.o.score_action(score.points, score.reason))
            self.o.ctx.done.add("birdsong")
        self.o.run_arm_actions()
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
            ctx.removals = []
            ctx.done.add("battles")
        o.run_arm_actions()
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
            ctx.arm_actions.append(o.losses_action(battle, result))
            ctx.dice.pop(0)
            log.info(f"battle in clearing {battle.clearing}: {result}")
            hits = result.defender_hits
            message = f"{battle.defender.value}: remove {hits} in clearing {battle.clearing}"
            if result.keep_lost:
                message += "; remove the Keep"  # тимчасово: AUT-8
            ctx.removals.append(message)
            o.status(step="daylight: battle", waiting_for=None)
            ctx.battle_index += 1
            o.run_arm_actions()
        return Status.SUCCESS


class WaitLossesRemoved(Step):
    """Після боїв рука вже прибрала втрати бота: рука в home, люди знімають свої втрати з усіх боїв
    і натискають «Хід бота»; лише тоді Rescan. Без боїв — одразу SUCCESS. «Хід бота», натиснутий
    раніше в цьому ході, очікування не пропускає."""

    def initialise(self) -> None:
        self.waiting = False

    def step(self) -> Status:
        ctx = self.o.ctx
        if not ctx.battles:
            return Status.SUCCESS
        if not self.waiting:
            self.o.arm.home()
            ctx.resume, self.waiting = False, True
            self.o.wait_for(
                WaitingFor.YOUR_TURN,
                "daylight: remove your pieces, then press Bot turn",
                message="; ".join(ctx.removals),
            )
        if not ctx.resume:
            return Status.RUNNING
        log.info("losses removed by the humans: rescanning the battle clearings")
        ctx.resume = False
        self.o.status(step="daylight: scanning the battle clearings", waiting_for=None, message="")
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
        o = self.o
        if "moves" not in o.ctx.done:
            o.ctx.actions = list(bot.moves(o.ctx.state, o.card(), **o.game["move"]))
            o.ctx.done.add("moves")
        o.status(step="daylight: move", waiting_for=None)
        o.run_arm_actions()
        while o.ctx.actions:
            move = o.ctx.actions[0]
            log.info(f"{move}")
            o.ctx.arm_actions.append(o.move_action(move))
            o.ctx.actions.pop(0)
            o.run_arm_actions()
        return Status.SUCCESS


class Recruits(Step):
    def step(self) -> Status:
        o = self.o
        if "recruits" not in o.ctx.done:
            recruits = bot.recruits(o.ctx.state, o.card(), **o.game["recruit"])
            o.ctx.restart_day = recruits is None  # карта без вартості крафту
            o.ctx.actions = list(recruits or [])
            o.ctx.done.add("recruits")
        o.status(step="daylight: recruit", waiting_for=None)
        o.run_arm_actions()
        while o.ctx.actions:
            recruit = o.ctx.actions[0]
            log.info(f"{recruit}")
            o.ctx.arm_actions.append(o.recruit_action(recruit))
            o.ctx.actions.pop(0)
            o.run_arm_actions()
        return Status.SUCCESS


class Evening(Step):
    def step(self) -> Status:
        in_schedule = max(self.o.schedule_size - self.o.ctx.cards_used, 0)
        draw = bot.evening(in_schedule, **self.o.game["evening"])
        log.info(f"evening: draw {draw} order cards")  # добирають люди: AUT-9
        self.o.status(step="evening", message=f"draw {draw} order cards into the Schedule")
        return Status.SUCCESS


class FinishTurn(Step):
    """Рука в home; лише після успіху хід завершено. Невдача — повтор, потім людина, а не новий
    хід."""

    def step(self) -> Status:
        self.o.arm.home()
        self.o.ctx.in_turn = self.o.ctx.turn_requested = False
        log.info(f"=== bot turn done, score {self.o.ctx.state.scores.get(BOT, 0)} ===")
        self.o.wait_for(WaitingFor.YOUR_TURN, "idle")
        return Status.SUCCESS


class AbortTurn(Step):
    """Хід дав FAILURE: хтось набрав game.win_points ПО (люди — NobodyWon, бот — GameOver, щойно
    його ПО додано) або впав крок без повтору (StartTurn, WaitOrderCard). Рука в home (збій
    руки — лише в лозі й статусі), незавершені дії забуто, хід завершено."""

    def step(self) -> Status:
        self.o.home_or_report()
        self.o.drop_arm_actions()
        self.o.ctx.in_turn = self.o.ctx.turn_requested = False
        if not self.o.ctx.game_over:
            log.error(f"bot turn aborted: {self.o.ctx.error}")
            self.o.wait_for(WaitingFor.YOUR_TURN, "turn aborted", error=self.o.ctx.error)
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
        if self.decorated.status != Status.FAILURE or self.o.ctx.game_over:
            return self.decorated.status
        self.failures += 1
        if self.failures <= self.o.step_retries:
            log.warning(f"{self.decorated.name}: 🟡 retry {self.failures}/{self.o.step_retries}")
            return Status.RUNNING
        log.error(f"{self.decorated.name}: 🔴 needs help, waiting for «bot turn» to continue")
        self.o.home_or_report()
        self.o.ctx.resume, self.waiting = False, True
        self.o.wait_for(
            WaitingFor.YOUR_TURN, f"needs help: {self.decorated.name}", error=self.o.ctx.error
        )
        return Status.RUNNING

    def terminate(self, new_status: Status) -> None:
        if new_status != Status.RUNNING:
            self.failures, self.waiting = 0, False
