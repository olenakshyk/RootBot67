"""BoardObservation — що vision повертає в app.

Зовнішній опис: docs/architecture.md -> vision -> observation.

`pieces` містить лише галявини, які було видно на кадрах (у тому числі порожні): при частковому
огляді Orchestrator оновлює тільки їх.

RecheckRequest — що детектору варто переглянути, щоб покращити спостереження;
як саме, вирішує Scanner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rootbot.common.types import BoardPoint, ClearingId, Faction, PieceCounts
from rootbot.vision.board_mask import BoardMask


@dataclass
class BoardObservation:
    pieces: dict[ClearingId, dict[Faction, PieceCounts]] = field(default_factory=dict)
    # галявина, де розпізнано Keep; None — Keep не знайдено або його галявину видно цілком без нього
    keep_clearing: ClearingId | None = None
    # де стоїть кожна фігурка: точка основи в мм дошки (вільні місця з цього рахує MovePlanner)
    positions: dict[ClearingId, dict[Faction, list[BoardPoint]]] = field(default_factory=dict)
    # ПО з доріжки рахунку; кого немає — не прочитано
    scores: dict[Faction, int] = field(default_factory=dict)
    confidence: float = 0.0  # 0..1


@dataclass(frozen=True)
class RecheckRequest:
    """Що переглянути ще раз і чому (Scanner обирає позу за причиною: інший кут, ближче, …)."""

    clearings: list[ClearingId]
    reason: str  # наприклад "not visible", "disagree", "low confidence", "after battles"
    uncovered: BoardMask | None = None  # для "not visible": яку частину дошки ще не видно
