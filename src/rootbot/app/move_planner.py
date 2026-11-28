"""MovePlanner — дії бота -> конкретні переміщення фігурок (PieceMoveTask).

Зовнішній опис: docs/architecture.md -> app -> MovePlanner;
вимоги: docs/requirements/orchestrator.md.

Приклад: Move(3 -> 7, 2 воїни) -> яких саме двох котів узяти на галявині 3 і на які вільні місця
галявини 7 поставити.

Детектор каже лише, хто де стоїть: фігурки по фракціях (BoardObservation.positions, звідси коти
бота) і всі зайняті точки (BoardObservation.occupied: фігурки будь-якої фракції й виду, разом
з Keep). Вільне місце планувальник шукає сам: точка в колі галявини (центр і радіус з мапи),
найближча до центру, що не ближче за board.piece_spacing_mm до будь-якої зайнятої точки. Коли в колі
такої точки немає, пошук іде далі за коло, поки точка лежить на дошці (board.size_mm) і найближча
до центру саме цієї галявини: детектор зараховує фігурку на найближчу галявину, тож там вона
лічиться на цю. Коли немає й там, бере найменш тісну точку кола — найдальшу від найближчої зайнятої
точки.

Заплановане планувальник тримає як цілі (goals): переміщення, яких рука ще не виконала, разом
з жетоном рахунку. Кота, який уже є джерелом цілі, він удруге не бере, а місця, куди цілі ставлять
фігурки, вважає зайнятими. Картину дошки (bot_positions, occupied) змінює лише done(task) — коли
рука справді перенесла фігурку; drop_goals() забуває всі цілі (перерваний хід). Огляд між
плануванням і done може трохи зсунути ту саму фігурку, тож точку кота з джерелом цілі він зіставляє
в межах spacing_mm / 2.

Огляд оновлює картину лише на галявинах, які бачив; решта галявин зберігає відомі планувальнику
місця, а цілі лишаються як є. Коли треба взяти з галявини або поставити на галявину, якої
планувальник не знає, він просить огляд — ClearingNeedsScan: Orchestrator оглядає цю галявину й
повторює крок. Не знає — це для взяття менше відомих місць котів бота, не зайнятих цілями, ніж
треба взяти, а для постановки — галявина, якої ще не покрив жоден огляд.

Доріжка рахунку (board.score_track_mm) порожня — жетон рахунку бота не рухається, з попередженням;
інакше в ній рівно game.win_points + 1 клітинка (0..win_points).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.common.types import BoardPoint, ClearingId
from rootbot.game.actions import Move, Recruit
from rootbot.game.board import Board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY, Location, PieceMoveTask
from rootbot.vision.observation import BoardObservation

log = logging.getLogger(__name__)


class ClearingNeedsScan(TemporaryError):
    """Планувальник не знає галявину clearing досить, щоб узяти з неї чи поставити на неї:
    Orchestrator оглядає її й повторює крок."""

    def __init__(self, clearing: ClearingId, reason: str) -> None:
        super().__init__(f"clearing {clearing} needs a scan: {reason}")
        self.clearing = clearing


@dataclass(frozen=True)
class _Goal:
    """Заплановане переміщення й галявини його кінців (SUPPLY — запас, None — доріжка рахунку)."""

    task: PieceMoveTask
    source: ClearingId | Literal["supply"] | None
    target: ClearingId | Literal["supply"] | None


class MovePlanner:
    def __init__(
        self,
        board: Board,
        board_size_mm: tuple[float, float],
        score_track: list[BoardPoint],
        spacing_mm: float,
    ) -> None:
        self.board = board
        self.board_size_mm = board_size_mm  # ширина, висота дошки, мм
        self.score_track = score_track  # клітинки доріжки рахунку 0..win_points, мм дошки
        self.spacing_mm = spacing_mm  # мін. відстань між центрами фігурок (фігурка + пальці)
        self.bot_positions: dict[ClearingId, list[BoardPoint]] = {}  # де коти бота
        self.occupied: dict[ClearingId, list[BoardPoint]] = {}  # усі зайняті точки, з Keep
        self._goals: list[_Goal] = []

    def process(
        self, observation: BoardObservation, clearings: list[ClearingId] | None = None
    ) -> None:
        """Коти бота й зайняті точки на галявинах, які бачив огляд: на всіх з
        observation.positions або лише на вказаних clearings. Інші галявини зберігають відомі
        місця; цілі лишаються як є."""
        if clearings is None:
            clearings = list(observation.positions)
        for c in clearings:
            self.bot_positions[c] = list(observation.positions.get(c, {}).get(BOT, []))
            self.occupied[c] = list(observation.occupied.get(c, []))

    @property
    def goals(self) -> list[PieceMoveTask]:
        """Заплановані переміщення, яких рука ще не виконала, у порядку планування."""
        return [goal.task for goal in self._goals]

    def move(self, move: Move) -> list[PieceMoveTask]:
        """Цілі: move.count котів з move.src на вільні місця move.dst. Обидві галявини
        перевіряються до будь-якої зміни: невдалий виклик нічого не змінює."""
        self._require_scanned(move.dst)
        sources = self._take(move.src, move.count)
        return [
            self._plan(point, move.src, self.free_spot(move.dst), move.dst) for point in sources
        ]

    def recruit(self, recruit: Recruit) -> list[PieceMoveTask]:
        """Цілі: recruit.count котів із запасу на вільні місця recruit.clearing."""
        self._require_scanned(recruit.clearing)
        c = recruit.clearing
        return [self._plan(SUPPLY, SUPPLY, self.free_spot(c), c) for _ in range(recruit.count)]

    def remove(self, clearing: ClearingId, count: int) -> list[PieceMoveTask]:
        """Втрати бота, цілі: воїни з галявини назад у запас."""
        return [
            self._plan(point, clearing, SUPPLY, SUPPLY) for point in self._take(clearing, count)
        ]

    def done(self, task: PieceMoveTask) -> None:
        """Рука виконала task: ціль знято, картина дошки оновлена — на галявині source звільнена
        точка, найближча до source в межах spacing_mm / 2 (огляд міг трохи зсунути ту саму
        фігурку), на галявині target зайнята точка target. Ціль жетона рахунку лише знімається.
        Задача, якої немає серед цілей, — ValueError."""
        goal = next((goal for goal in self._goals if goal.task == task), None)
        if goal is None:
            raise ValueError(f"not a pending goal: {task}")
        self._goals.remove(goal)
        if goal.source not in (SUPPLY, None):
            self._release(goal.source, task.source)
        if goal.target not in (SUPPLY, None):
            self.bot_positions.setdefault(goal.target, []).append(task.target)
            self.occupied[goal.target].append(task.target)

    def drop_goals(self) -> None:
        """Забути всі цілі (перерваний хід); картина дошки лишається як є."""
        self._goals.clear()

    def score(self, old: int, new: int) -> list[PieceMoveTask]:
        """Ціль: жетон рахунку бота з клітинки old на new (понад win_points — на останню
        клітинку)."""
        if not self.score_track:
            log.warning("board.score_track_mm is not set: the score token is not moved")
            return []
        last = len(self.score_track) - 1
        old, new = min(old, last), min(new, last)
        if old == new:
            return []
        return [self._plan(self.score_track[old], None, self.score_track[new], None)]

    def free_spot(self, clearing: ClearingId) -> BoardPoint:
        """Найближче до центру місце галявини, вільне від зайнятих точок (фігурки, Keep).

        Кільця пошуку йдуть від центру через spacing_mm / 2. За radius_mm на кільці лишаються
        точки на дошці, найближчі до центру цієї галявини (_counts_as): детектор зарахує фігурку
        там на цю галявину. Пошук закінчується на першому кільці без таких точок. Коли вільного
        місця немає й там — найменш тісне місце в колі галявини: точка, найдальша від найближчої
        до неї зайнятої точки. Місця, куди ставлять фігурки цілі, теж зайняті. Галявина, якої
        планувальник не знає, — ClearingNeedsScan."""
        self._require_scanned(clearing)
        c = self.board[clearing]
        planned = [goal.task.target for goal in self._goals if goal.target == clearing]
        taken = self.occupied[clearing] + planned
        best, best_clearance = c.center, -1.0
        for r, ring in _rings(c.center, self.spacing_mm / 2):
            inside = r <= c.radius_mm
            if not inside:
                ring = [point for point in ring if self._counts_as(point, clearing)]
                if not ring:
                    break
            for point in ring:
                clearance = _clearance(point, taken)
                if clearance >= self.spacing_mm:
                    return point
                if inside and clearance > best_clearance:
                    best, best_clearance = point, clearance
        log.warning(
            f"clearing {clearing}: no free spot, nearest piece {best_clearance:.0f} mm away"
        )
        return best

    def _counts_as(self, point: BoardPoint, clearing: ClearingId) -> bool:
        """Точка лежить на дошці й ближча до центру clearing, ніж до центру будь-якої іншої
        галявини: детектор зарахує фігурку в ній на clearing."""
        w, h = self.board_size_mm
        if not (0 <= point.x <= w and 0 <= point.y <= h):
            return False
        own = point.distance_to(self.board[clearing].center)
        others = (c.center for c in self.board.clearings.values() if c.id != clearing)
        return all(own < point.distance_to(center) for center in others)

    def _require_scanned(self, clearing: ClearingId) -> None:
        """ClearingNeedsScan, якщо галявину не покрив жоден огляд."""
        if clearing not in self.occupied:
            raise ClearingNeedsScan(clearing, "no scan has covered it")

    def _take(self, clearing: ClearingId, count: int) -> list[BoardPoint]:
        """count відомих місць котів бота на clearing, що не ближче за spacing_mm / 2 до джерела
        жодної цілі (огляд міг трохи зсунути ту саму фігурку). Таких місць менше —
        ClearingNeedsScan."""
        planned = [goal.task.source for goal in self._goals if goal.source == clearing]
        positions = self.bot_positions.get(clearing, [])
        free = [point for point in positions if self._nearest(planned, point) is None]
        if len(free) < count:
            reason = f"{len(free)} known bot piece positions, {count} needed"
            raise ClearingNeedsScan(clearing, reason)
        return free[:count]

    def _release(self, clearing: ClearingId, point: BoardPoint) -> None:
        """Прибирає з bot_positions і occupied галявини точку, найближчу до point у межах
        spacing_mm / 2."""
        for points in (self.bot_positions.get(clearing, []), self.occupied[clearing]):
            nearest = self._nearest(points, point)
            if nearest is None:
                log.debug(f"clearing {clearing}: no piece near the moved one to release")
            else:
                points.remove(nearest)

    def _nearest(self, points: list[BoardPoint], point: BoardPoint) -> BoardPoint | None:
        """Точка з points, найближча до point, якщо вона не далі за spacing_mm / 2."""
        near = [q for q in points if q.distance_to(point) <= self.spacing_mm / 2]
        return min(near, key=point.distance_to, default=None)

    def _plan(
        self,
        source: Location,
        source_clearing: ClearingId | Literal["supply"] | None,
        target: Location,
        target_clearing: ClearingId | Literal["supply"] | None,
    ) -> PieceMoveTask:
        """Нова ціль source -> target; повертає її задачу."""
        task = PieceMoveTask(source, target)
        self._goals.append(_Goal(task, source_clearing, target_clearing))
        return task


def _rings(center: BoardPoint, step: float) -> Iterator[tuple[float, list[BoardPoint]]]:
    """Кандидати на вільне місце кільцями (радіус, точки): центр, потім кільця через `step` мм,
    на кожному кільці точки через ~`step` мм. Перший вільний = найближчий до центру."""
    yield 0.0, [center]
    r = step
    while True:
        n = max(int(2 * math.pi * r / step), 1)
        angles = (2 * math.pi * i / n for i in range(n))
        ring = [BoardPoint(center.x + r * math.cos(a), center.y + r * math.sin(a)) for a in angles]
        yield r, ring
        r += step


def _clearance(point: BoardPoint, taken: list[BoardPoint]) -> float:
    """Відстань від point до найближчої фігурки з taken, мм; без фігурок — нескінченність."""
    return min((point.distance_to(q) for q in taken), default=math.inf)


def create_move_planner(cfg: dict, board: Board) -> MovePlanner:
    """MovePlanner з config. board.score_track_mm порожній або без значення — жетон рахунку не
    рухається; інакше рівно game.win_points + 1 точка (клітинки 0..win_points), інша довжина —
    FatalError."""
    raw_track = cfg["board"].get("score_track_mm") or []
    cells = cfg["game"]["win_points"] + 1
    if raw_track and len(raw_track) != cells:
        raise FatalError(
            f"board.score_track_mm: expected {cells} points (0..{cells - 1}), got {len(raw_track)}"
        )
    track = [BoardPoint(float(x), float(y)) for x, y in raw_track]
    width, height = cfg["board"]["size_mm"]
    size = (float(width), float(height))
    return MovePlanner(board, size, track, cfg["board"]["piece_spacing_mm"])
