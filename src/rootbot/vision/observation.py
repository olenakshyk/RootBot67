"""BoardObservation — що vision повертає в app.

Зовнішній опис: docs/architecture.md -> vision -> observation.

`pieces`, `positions` і `occupied` містять лише галявини, які було видно на кадрах (у тому числі
порожні): при частковому огляді Orchestrator оновлює тільки їх.

RecheckRequest — що детектору варто переглянути, щоб покращити спостереження;
як саме, вирішує Scanner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from rootbot.common.types import BoardPoint, ClearingId, Faction, PieceCounts
from rootbot.vision.board_mask import BoardMask


@dataclass
class BoardObservation:
    pieces: dict[ClearingId, dict[Faction, PieceCounts]] = field(default_factory=dict)
    # галявина, де розпізнано Keep; None — Keep не знайдено або його галявину видно цілком без нього
    keep_clearing: ClearingId | None = None
    # де стоїть кожна фігурка з `pieces`, по фракціях: точка основи в мм дошки (звідси рука бере
    # фігурки бота)
    positions: dict[ClearingId, dict[Faction, list[BoardPoint]]] = field(default_factory=dict)
    # усі зайняті точки галявини: основа кожної фігурки будь-якої фракції й виду, разом з Keep
    # (вільні місця з цього рахує MovePlanner)
    occupied: dict[ClearingId, list[BoardPoint]] = field(default_factory=dict)
    # ПО з доріжки рахунку; кого немає — не прочитано
    scores: dict[Faction, int] = field(default_factory=dict)
    confidence: float = 0.0  # 0..1


class RecheckReason(Enum):
    """Чому галявини треба переглянути ще раз."""

    NOT_VISIBLE = "not visible"  # кадри ще не покривають ціль огляду
    DISAGREE = "disagree"  # кадри дають різну кількість фігурок на галявині
    LOW_CONFIDENCE = "low confidence"  # рамки з впевненістю нижче sure_confidence
    AFTER_BATTLES = "after battles"  # після битв: фігурки на галявинах змінились
    NEEDS_SCAN = "needs scan"  # планувальнику бракує даних про галявину


@dataclass(frozen=True)
class RecheckRequest:
    """Що переглянути ще раз і чому (Scanner обирає позу за причинами: інший кут, ближче, …).

    `reasons` — усі причини запиту; як їх ураховувати, вирішує модуль, що обробляє запит."""

    clearings: list[ClearingId]
    reasons: list[RecheckReason]
    uncovered: BoardMask | None = None  # для NOT_VISIBLE: яку частину дошки ще не видно
