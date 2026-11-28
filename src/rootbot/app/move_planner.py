"""MovePlanner — дії бота -> конкретні переміщення фігурок (PieceMoveTask).

Зовнішній опис: docs/architecture.md -> app -> MovePlanner;
вимоги: docs/requirements/orchestrator.md.

Приклад: Move(3 -> 7, 2 воїни) -> яких саме двох котів узяти на галявині 3 і на які вільні місця
галявини 7 поставити.

Детектор каже лише, хто де стоїть (BoardObservation.positions). Вільне місце планувальник шукає сам:
точка в колі галявини (центр і радіус з мапи), найближча до центру, що не ближче за
board.piece_spacing_mm до будь-якої фігурки. Коли такої точки немає, бере найменш тісну — найдальшу
від найближчої фігурки. Він пам'ятає заплановане: узяте місце звільняється,
поставлене — займається, тож наступні дії того самого ходу бачать актуальну дошку.

Якщо позиції котів не відомі, береться центр галявини: Manipulator уточнює місце кадром зблизька.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator

from rootbot.common.types import BoardPoint, ClearingId
from rootbot.game.actions import Move, Recruit
from rootbot.game.board import Board
from rootbot.game.state import BOT
from rootbot.robot.manipulator import SUPPLY, PieceMoveTask
from rootbot.vision.observation import BoardObservation

log = logging.getLogger(__name__)


class MovePlanner:
    def __init__(self, board: Board, score_track: list[BoardPoint], spacing_mm: float) -> None:
        self.board = board
        self.score_track = score_track  # клітинки доріжки рахунку 0..30, мм дошки
        self.spacing_mm = spacing_mm  # мін. відстань між центрами фігурок (фігурка + пальці)
        self.bot_positions: dict[ClearingId, list[BoardPoint]] = {}  # де коти бота
        self.occupied: dict[ClearingId, list[BoardPoint]] = {}  # де будь-які фігурки

    def process(
        self, observation: BoardObservation, clearings: list[ClearingId] | None = None
    ) -> None:
        """Хто де стоїть з огляду: усієї дошки або лише вказаних галявин."""
        if clearings is None:
            self.bot_positions, self.occupied = {}, {}
            clearings = list(observation.positions)
        for c in clearings:
            here = observation.positions.get(c, {})
            self.bot_positions[c] = list(here.get(BOT, []))
            self.occupied[c] = [point for points in here.values() for point in points]

    def move(self, move: Move) -> list[PieceMoveTask]:
        return [PieceMoveTask(self._take(move.src), self._put(move.dst)) for _ in range(move.count)]

    def recruit(self, recruit: Recruit) -> list[PieceMoveTask]:
        return [PieceMoveTask(SUPPLY, self._put(recruit.clearing)) for _ in range(recruit.count)]

    def remove(self, clearing: ClearingId, count: int) -> list[PieceMoveTask]:
        """Втрати бота: воїни з галявини назад у запас."""
        return [PieceMoveTask(self._take(clearing), SUPPLY) for _ in range(count)]

    def score(self, old: int, new: int) -> list[PieceMoveTask]:
        """Жетон рахунку бота з клітинки old на new (понад 30 — на 30)."""
        if not self.score_track:
            log.warning("board.score_track_mm is not set: the score token is not moved")
            return []
        last = len(self.score_track) - 1
        old, new = min(old, last), min(new, last)
        if old == new:
            return []
        return [PieceMoveTask(self.score_track[old], self.score_track[new])]

    def free_spot(self, clearing: ClearingId) -> BoardPoint:
        """Найближче до центру місце галявини, вільне від інших фігурок. Коли вільного місця
        немає — найменш тісне: точка, найдальша від найближчої до неї фігурки."""
        c = self.board[clearing]
        taken = self.occupied.get(clearing, [])
        best, best_clearance = c.center, -1.0
        for point in _candidates(c.center, c.radius_mm, self.spacing_mm / 2):
            clearance = _clearance(point, taken)
            if clearance >= self.spacing_mm:
                return point
            if clearance > best_clearance:
                best, best_clearance = point, clearance
        log.warning(
            f"clearing {clearing}: no free spot, nearest piece {best_clearance:.0f} mm away"
        )
        return best

    def _take(self, clearing: ClearingId) -> BoardPoint:
        positions = self.bot_positions.get(clearing)
        point = positions.pop(0) if positions else self.board[clearing].center
        if point in self.occupied.get(clearing, []):
            self.occupied[clearing].remove(point)
        return point

    def _put(self, clearing: ClearingId) -> BoardPoint:
        point = self.free_spot(clearing)
        self.bot_positions.setdefault(clearing, []).append(point)
        self.occupied.setdefault(clearing, []).append(point)
        return point


def _candidates(center: BoardPoint, max_r: float, step: float) -> Iterator[BoardPoint]:
    """Кандидати на вільне місце: центр, потім кільця через `step` мм до `max_r`,
    на кожному кільці точки через ~`step` мм. Перший вільний = найближчий до центру."""
    yield center
    r = step
    while r <= max_r:
        n = max(int(2 * math.pi * r / step), 1)
        for i in range(n):
            angle = 2 * math.pi * i / n
            yield BoardPoint(center.x + r * math.cos(angle), center.y + r * math.sin(angle))
        r += step


def _clearance(point: BoardPoint, taken: list[BoardPoint]) -> float:
    """Відстань від point до найближчої фігурки з taken, мм; без фігурок — нескінченність."""
    return min((math.dist((point.x, point.y), (q.x, q.y)) for q in taken), default=math.inf)


def create_move_planner(cfg: dict, board: Board) -> MovePlanner:
    track = [BoardPoint(float(x), float(y)) for x, y in cfg["board"].get("score_track_mm", [])]
    return MovePlanner(board, track, cfg["board"]["piece_spacing_mm"])
