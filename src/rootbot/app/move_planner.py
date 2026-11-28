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
точки. Він пам'ятає заплановане: узяте місце звільняється, поставлене — займається, тож наступні
дії того самого ходу бачать актуальну дошку.

Огляд оновлює лише галявини, які бачив; решта галявин зберігає відомі планувальнику місця.
Коли треба взяти з галявини або поставити на галявину, якої планувальник не знає, він просить
огляд — ClearingNeedsScan: Orchestrator оглядає цю галявину й повторює крок. Не знає — це для
взяття менше відомих місць котів бота, ніж треба взяти, а для постановки — галявина, якої ще не
покрив жоден огляд і на яку нічого не заплановано.

Доріжка рахунку (board.score_track_mm) порожня — жетон рахунку бота не рухається, з попередженням;
інакше в ній рівно game.win_points + 1 клітинка (0..win_points).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.common.types import BoardPoint, ClearingId
from rootbot.game.actions import Move, Recruit
from rootbot.game.board import Board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY, PieceMoveTask
from rootbot.vision.observation import BoardObservation

log = logging.getLogger(__name__)


class ClearingNeedsScan(TemporaryError):
    """Планувальник не знає галявину clearing досить, щоб узяти з неї чи поставити на неї:
    Orchestrator оглядає її й повторює крок."""

    def __init__(self, clearing: ClearingId, reason: str) -> None:
        super().__init__(f"clearing {clearing} needs a scan: {reason}")
        self.clearing = clearing


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

    def process(
        self, observation: BoardObservation, clearings: list[ClearingId] | None = None
    ) -> None:
        """Коти бота й зайняті точки на галявинах, які бачив огляд: на всіх з
        observation.positions або лише на вказаних clearings. Інші галявини зберігають відомі
        місця, також заплановані тут."""
        if clearings is None:
            clearings = list(observation.positions)
        for c in clearings:
            self.bot_positions[c] = list(observation.positions.get(c, {}).get(BOT, []))
            self.occupied[c] = list(observation.occupied.get(c, []))

    def move(self, move: Move) -> list[PieceMoveTask]:
        """Обидві галявини перевіряються до будь-якої зміни: невдалий виклик нічого не змінює."""
        self._require_scanned(move.dst)
        return [
            PieceMoveTask(point, self._put(move.dst)) for point in self._take(move.src, move.count)
        ]

    def recruit(self, recruit: Recruit) -> list[PieceMoveTask]:
        self._require_scanned(recruit.clearing)
        return [PieceMoveTask(SUPPLY, self._put(recruit.clearing)) for _ in range(recruit.count)]

    def remove(self, clearing: ClearingId, count: int) -> list[PieceMoveTask]:
        """Втрати бота: воїни з галявини назад у запас."""
        return [PieceMoveTask(point, SUPPLY) for point in self._take(clearing, count)]

    def score(self, old: int, new: int) -> list[PieceMoveTask]:
        """Жетон рахунку бота з клітинки old на new (понад win_points — на останню клітинку)."""
        if not self.score_track:
            log.warning("board.score_track_mm is not set: the score token is not moved")
            return []
        last = len(self.score_track) - 1
        old, new = min(old, last), min(new, last)
        if old == new:
            return []
        return [PieceMoveTask(self.score_track[old], self.score_track[new])]

    def free_spot(self, clearing: ClearingId) -> BoardPoint:
        """Найближче до центру місце галявини, вільне від зайнятих точок (фігурки, Keep).

        Кільця пошуку йдуть від центру через spacing_mm / 2. За radius_mm на кільці лишаються
        точки на дошці, найближчі до центру цієї галявини (_counts_as): детектор зарахує фігурку
        там на цю галявину. Пошук закінчується на першому кільці без таких точок. Коли вільного
        місця немає й там — найменш тісне місце в колі галявини: точка, найдальша від найближчої
        до неї зайнятої точки. Галявина, якої планувальник не знає, — ClearingNeedsScan."""
        self._require_scanned(clearing)
        c = self.board[clearing]
        taken = self.occupied.get(clearing, [])
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
        """ClearingNeedsScan, якщо галявину не покрив жоден огляд і на неї нічого не заплановано."""
        if clearing not in self.occupied:
            raise ClearingNeedsScan(clearing, "no scan has covered it")

    def _take(self, clearing: ClearingId, count: int) -> list[BoardPoint]:
        """count відомих місць котів бота на clearing; ці місця звільняються. Відомих місць менше —
        ClearingNeedsScan до будь-якої зміни."""
        positions = self.bot_positions.get(clearing, [])
        if len(positions) < count:
            reason = f"{len(positions)} known bot piece positions, {count} needed"
            raise ClearingNeedsScan(clearing, reason)
        points, self.bot_positions[clearing] = positions[:count], positions[count:]
        for point in points:
            if point in self.occupied.get(clearing, []):
                self.occupied[clearing].remove(point)
        return points

    def _put(self, clearing: ClearingId) -> BoardPoint:
        point = self.free_spot(clearing)
        self.bot_positions.setdefault(clearing, []).append(point)
        self.occupied.setdefault(clearing, []).append(point)
        return point


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
