"""Detector — головний клас зору: кадри огляду -> хто де стоїть (BoardObservation).

Зовнішній опис: docs/architecture.md -> vision -> Detector; вимоги: docs/requirements/detector.md.

Три публічні кроки, у такому порядку (цикли й ліміт спроб веде Orchestrator):
1. add_frames(frames, target) — покриття, лише гомографія (board_detector), без моделі.
   Ціль огляду — BoardMask (уся дошка, галявини, розширені на vision.clearing_margin,
   або інша маска). Повертає RecheckRequest("not visible", uncovered=маска непокритого),
   поки кадри покривають менше vision.min_coverage цілі, або None. Кадри накопичуються;
   новий огляд починається з `target` або першим add_frames після detect().
2. detect() — перевіряє покриття (неповне — попередження в лог, розпізнає з тим, що є) і запускає
   модель (PieceDetectorModel) один раз на всіх накопичених кадрах. Повертає чернетку
   спостереження й один RecheckRequest з усіма сумнівними галявинами — або None. Причини
   (у reason через кому): "disagree" (кадри дають різну кількість фігурок на галявині),
   "low confidence" (рамки з впевненістю між min_confidence і sure_confidence).
3. refine(observation, frames) — лише після detect(): розпізнає нові кадри й оновлює галявини,
   які на них видно цілком.

Рамка -> точка основи -> мм дошки -> найближча галявина (види ON_CLEARINGS, точки в межах дошки).
Та сама фігурка з різних кадрів (фракція, вид, ближче за vision.merge_mm) — одна фігурка.

Ще не зроблено: маркери рахунку -> scores, запас котів на планшеті бота, поза дошки в базі робота.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field, replace

from rootbot.common.types import BoardPoint, ClearingId, Faction, PieceCounts, PieceKind
from rootbot.game.board import Board
from rootbot.vision.board_detector import BoardDetector, BoardHomography, create_board_detector
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.camera import Frame
from rootbot.vision.observation import BoardObservation, RecheckRequest
from rootbot.vision.piece_detector_model import PieceDetectorModel

log = logging.getLogger(__name__)

Result = tuple[BoardObservation, RecheckRequest | None]  # чернетка + що ще переглянути
View = tuple[Frame, BoardHomography]  # кадр, на якому знайдено дошку
COUNTED = {
    PieceKind.WARRIOR: "warriors",
    PieceKind.BUILDING: "buildings",
    PieceKind.TOKEN: "tokens",
    PieceKind.PAWN: "pawn",
}
ON_CLEARINGS = {*COUNTED, PieceKind.KEEP}  # види, що стоять на галявинах


@dataclass(frozen=True)
class _Located:
    """Одна рамка, переведена на дошку."""

    frame: int  # номер кадру в наборі
    clearing: ClearingId
    faction: Faction
    kind: PieceKind
    point: BoardPoint  # точка основи, мм дошки
    confidence: float


@dataclass
class _Piece:
    """Одна фігурка після зведення кадрів."""

    clearing: ClearingId
    faction: Faction
    kind: PieceKind
    points: list[BoardPoint] = field(default_factory=list)
    frames: set[int] = field(default_factory=set)

    def point(self) -> BoardPoint:
        n = len(self.points)
        return BoardPoint(sum(p.x for p in self.points) / n, sum(p.y for p in self.points) / n)


class Detector:
    def __init__(
        self,
        board: Board,
        board_detector: BoardDetector,
        model: PieceDetectorModel,
        *,
        min_confidence: float,
        sure_confidence: float,
        merge_mm: float,
        min_coverage: float,
        clearing_margin: float,
        mask_mm_per_px: float,
    ) -> None:
        self.board = board
        self.board_detector = board_detector
        self.model = model
        self.min_confidence = min_confidence
        self.sure_confidence = sure_confidence
        self.merge_mm = merge_mm
        self.min_coverage = min_coverage
        self.clearing_margin = clearing_margin
        self.mask_mm_per_px = mask_mm_per_px
        self._target = self.board_mask()  # що треба побачити в поточному огляді
        self._views: list[View] = []  # кадри поточного огляду
        # detect() уже був: refine() дозволено, add_frames() почне новий огляд
        self._detected = False

    # --- огляд ---

    def add_frames(
        self, frames: list[Frame], *, target: BoardMask | None = None
    ) -> RecheckRequest | None:
        """Додати кадри огляду. Повертає, чого ще не видно ("not visible"), або None — ціль покрито.

        `target` починає новий огляд з цією ціллю; без нього кадри додаються до поточного огляду,
        а після detect() починається новий огляд усієї дошки."""
        if target is not None or self._detected:
            self._target = target if target is not None else self.board_mask()
            self._views, self._detected = [], False
        self._views += self._find_boards(frames)
        uncovered = self._uncovered()
        if uncovered is None:
            return None
        return RecheckRequest(self._touching(uncovered), "not visible", uncovered)

    def detect(self) -> Result:
        """Розпізнати накопичені кадри огляду (модель — один раз на всіх)."""
        uncovered = self._uncovered()
        if uncovered is not None:
            coverage = 1 - uncovered.area() / max(self._target.area(), 1)
            log.warning(f"only {coverage:.0%} of the target is visible, detecting with that")
        self._detected = True
        return self._observe(BoardObservation(), self._views)

    def refine(self, observation: BoardObservation, frames: list[Frame]) -> Result:
        """Уточнити спостереження новими кадрами: оновлюються галявини, які на них видно цілком."""
        if not self._detected:
            raise RuntimeError("Detector.refine() before detect()")
        copy = replace(
            observation, pieces=dict(observation.pieces), positions=dict(observation.positions)
        )
        return self._observe(copy, self._find_boards(frames))

    # --- цілі огляду ---

    def board_mask(self) -> BoardMask:
        """Уся дошка."""
        return BoardMask.whole(self.board_detector.board_size_mm, self.mask_mm_per_px)

    def clearings_mask(self, clearings: list[ClearingId]) -> BoardMask:
        """Галявини, кожна розширена на vision.clearing_margin."""
        whole = self.board_mask()
        mask = whole.empty()
        for c in clearings:
            clearing = self.board[c]
            mask = mask | whole.circle(
                clearing.center, clearing.radius_mm * (1 + self.clearing_margin)
            )
        return mask

    # --- кроки ---

    def _uncovered(self) -> BoardMask | None:
        """Непокрита частина цілі, якщо покрито менше min_coverage; інакше None."""
        homographies = [view for _, view in self._views]
        if self._target.coverage(homographies) >= self.min_coverage:
            return None
        return self._target - self._target.visible_on(homographies)

    def _observe(self, observation: BoardObservation, views: list[View]) -> Result:
        """Модель на `views` -> фігурки на галявинах, які видно цілком -> чернетка + що уточнити."""
        covered = self._covered(views)
        located = self._locate_pieces(views)
        self._fill(observation, self._merge(located), covered)
        recheck = self._quality_recheck(located, views, covered)
        issues = recheck.clearings if recheck else []
        observation.confidence = 1 - len(issues) / len(covered) if covered else 0.0
        return observation, recheck

    def _find_boards(self, frames: list[Frame]) -> list[View]:
        """Дошка на кожному кадрі; кадри без дошки відкидаються."""
        views = [(f, v) for f in frames if (v := self.board_detector.find(f)) is not None]
        if len(views) < len(frames):
            log.warning(f"board not found on {len(frames) - len(views)}/{len(frames)} frames")
        return views

    def _touching(self, mask: BoardMask) -> list[ClearingId]:
        """Галявини, яких торкається маска."""
        return [
            c.id
            for c in self.board.by_priority()
            if (mask & mask.circle(c.center, c.radius_mm)).area() > 0
        ]

    def _covered(self, views: list[View]) -> set[ClearingId]:
        """Галявини, які хоч один кадр бачить цілком."""
        return {c for _, view in views for c in self._sees(view)}

    def _sees(self, view: BoardHomography) -> set[ClearingId]:
        return {c.id for c in self.board.clearings.values() if view.sees(c.center, c.radius_mm)}

    def _locate_pieces(self, views: list[View]) -> list[_Located]:
        """Модель на всіх кадрах одним викликом -> рамки на дошці, по галявинах."""
        boxes = self.model.detect([frame for frame, _ in views])
        located = []
        for i, ((_, view), frame_boxes) in enumerate(zip(views, boxes, strict=True)):
            for box in frame_boxes:
                if box.faction is None or box.confidence < self.min_confidence:
                    continue  # нічиє (кубик) — не фігурка на галявині
                if box.kind not in ON_CLEARINGS:
                    continue  # маркер рахунку стоїть на доріжці рахунку
                point = view.to_board_mm(*box.base_px())
                clearing = self._clearing_at(point)
                if clearing is not None:
                    located.append(
                        _Located(i, clearing, box.faction, box.kind, point, box.confidence)
                    )
        return located

    def _clearing_at(self, point: BoardPoint) -> ClearingId | None:
        """Найближча до точки галявина; None — точка поза дошкою."""
        w, h = self.board_detector.board_size_mm
        if not (0 <= point.x <= w and 0 <= point.y <= h):
            return None
        return min(self.board.clearings.values(), key=lambda c: _dist(point, c.center)).id

    def _merge(self, located: list[_Located]) -> list[_Piece]:
        """Та сама фігурка з різних кадрів (фракція, вид, поруч) — одна."""
        pieces: list[_Piece] = []
        for r in located:
            same = next(
                (
                    p
                    for p in pieces
                    if (p.clearing, p.faction, p.kind) == (r.clearing, r.faction, r.kind)
                    and r.frame not in p.frames
                    and _dist(r.point, p.point()) <= self.merge_mm
                ),
                None,
            )
            if same is None:
                pieces.append(_Piece(r.clearing, r.faction, r.kind, [r.point], {r.frame}))
            else:
                same.points.append(r.point)
                same.frames.add(r.frame)
        return pieces

    def _fill(
        self, observation: BoardObservation, pieces: list[_Piece], clearings: set[ClearingId]
    ) -> None:
        """Записати кількості й позиції фігурок і Keep для `clearings` (інші галявини не чіпати)."""
        for clearing in clearings:
            observation.pieces[clearing] = {}
            observation.positions[clearing] = {}
        if observation.keep_clearing in clearings:
            observation.keep_clearing = None
        for piece in pieces:
            if piece.clearing not in clearings:
                continue
            if piece.kind is PieceKind.KEEP:
                observation.keep_clearing = piece.clearing
                continue
            attr = COUNTED.get(piece.kind)
            if attr is None:
                continue
            counts = observation.pieces[piece.clearing].setdefault(piece.faction, PieceCounts())
            setattr(counts, attr, getattr(counts, attr) + 1)
            observation.positions[piece.clearing].setdefault(piece.faction, []).append(
                piece.point()
            )

    def _quality_recheck(
        self, located: list[_Located], views: list[View], covered: set[ClearingId]
    ) -> RecheckRequest | None:
        """Усі сумнівні галявини одним запитом; reason — їхні причини через кому."""
        disagree = self._disagreeing(located, views) & covered
        low = {r.clearing for r in located if r.confidence < self.sure_confidence} & covered
        if not disagree | low:
            return None
        reasons = [
            name for name, found in (("disagree", disagree), ("low confidence", low)) if found
        ]
        return RecheckRequest(sorted(disagree | low), ", ".join(reasons))

    def _disagreeing(self, located: list[_Located], views: list[View]) -> set[ClearingId]:
        """Галявини, на яких кадри, що бачать їх цілком, дають різну кількість фігурок."""
        counts: dict[ClearingId, list[Counter[tuple[Faction, PieceKind]]]] = {}
        for i, (_, view) in enumerate(views):
            for clearing in self._sees(view):
                counter = Counter(
                    (r.faction, r.kind) for r in located if r.frame == i and r.clearing == clearing
                )
                counts.setdefault(clearing, []).append(counter)
        return {c for c, per_frame in counts.items() if any(x != per_frame[0] for x in per_frame)}


def _dist(a: BoardPoint, b: BoardPoint) -> float:
    return math.dist((a.x, a.y), (b.x, b.y))


def create_detector(cfg: dict, board: Board, model: PieceDetectorModel) -> Detector:
    v = cfg["vision"]
    return Detector(
        board,
        create_board_detector(cfg),
        model,
        min_confidence=v["min_confidence"],
        sure_confidence=v["sure_confidence"],
        merge_mm=v["merge_mm"],
        min_coverage=v["min_coverage"],
        clearing_margin=v["clearing_margin"],
        mask_mm_per_px=v["mask_mm_per_px"],
    )
