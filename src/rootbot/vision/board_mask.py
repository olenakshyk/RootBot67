"""BoardMask — частина дошки: що треба побачити (ціль огляду) або що ще не видно.

Вимоги: docs/requirements/detector.md (FR-V24).

Растр розміру дошки, 1 піксель = vision.mask_mm_per_px мм. Будується з усієї дошки, кола (галявина,
розширена на vision.clearing_margin) або іншої маски; маски об'єднуються (|) і віднімаються (-).
Покриття кадрами: видимий чотирикутник кожного кадру (BoardHomography.visible) зафарбовується на
масці, частка зафарбованого = coverage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from rootbot.common.types import BoardPoint
from rootbot.vision.board_detector import BoardHomography

Grid = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class BoardMask:
    grid: Grid  # True — точка дошки належить масці; [рядок = y, стовпець = x]
    mm_per_px: float

    @classmethod
    def whole(cls, board_size_mm: tuple[float, float], mm_per_px: float) -> BoardMask:
        """Уся дошка."""
        w, h = (round(s / mm_per_px) for s in board_size_mm)
        return cls(np.ones((h, w), dtype=bool), mm_per_px)

    def circle(self, center: BoardPoint, radius_mm: float) -> BoardMask:
        """Коло на тій самій сітці, що й ця маска (наприклад, галявина)."""
        grid = np.zeros(self.grid.shape, dtype=np.uint8)
        c = (round(center.x / self.mm_per_px), round(center.y / self.mm_per_px))
        cv2.circle(grid, c, round(radius_mm / self.mm_per_px), 1, -1)
        return BoardMask(grid.astype(bool), self.mm_per_px)

    def polygon(self, points: Sequence[BoardPoint]) -> BoardMask:
        """Багатокутник на тій самій сітці (наприклад, видима з кадру частина дошки)."""
        grid = np.zeros(self.grid.shape, dtype=np.uint8)
        poly = np.array([(p.x / self.mm_per_px, p.y / self.mm_per_px) for p in points], np.int32)
        cv2.fillPoly(grid, [poly], 1)
        return BoardMask(grid.astype(bool), self.mm_per_px)

    def empty(self) -> BoardMask:
        return BoardMask(np.zeros(self.grid.shape, dtype=bool), self.mm_per_px)

    def __or__(self, other: BoardMask) -> BoardMask:
        return BoardMask(self.grid | other.grid, self.mm_per_px)

    def __and__(self, other: BoardMask) -> BoardMask:
        return BoardMask(self.grid & other.grid, self.mm_per_px)

    def __sub__(self, other: BoardMask) -> BoardMask:
        return BoardMask(self.grid & ~other.grid, self.mm_per_px)

    def area(self) -> int:
        return int(self.grid.sum())

    def visible_on(self, views: Sequence[BoardHomography]) -> BoardMask:
        """Частина маски, яку видно хоч на одному кадрі."""
        seen = self.empty()
        for view in views:
            seen = seen | self.polygon(view.visible())
        return self & seen

    def coverage(self, views: Sequence[BoardHomography]) -> float:
        """Частка маски, яку видно на кадрах (1.0 — усю; порожня маска вважається покритою)."""
        total = self.area()
        return self.visible_on(views).area() / total if total else 1.0

    def centers(self) -> list[BoardPoint]:
        """Центри окремих ділянок маски, мм дошки (куди дивитися, щоб їх побачити)."""
        n, _, _, centroids = cv2.connectedComponentsWithStats(self.grid.astype(np.uint8))
        return [
            BoardPoint(float(x) * self.mm_per_px, float(y) * self.mm_per_px)
            for x, y in centroids[1:n]
        ]
