# Дії, які видає бот: бій, рух, набір, очки.
# Контракт з app: docs/architecture.md#ключові-інтерфейси.
from __future__ import annotations

from dataclasses import dataclass

from rootbot.common.types import ClearingId, Faction


@dataclass(frozen=True)
class Battle:
    clearing: ClearingId
    defender: Faction


@dataclass(frozen=True)
class BattleResult:
    """Наслідки одного бою після кидка кубиків. Які саме фігурки знімати, обирає їхній власник."""

    bot_warriors_lost: int
    keep_lost: bool  # Keep знімається, лише коли на галявині не лишилося воїнів бота
    # Скільки фігурок знімає захисник (не більше, ніж у нього є на галявині).
    # У Бродяги це пошкоджені предмети, а не фігурки, і без обрізання.
    defender_hits: int


@dataclass(frozen=True)
class Move:
    src: ClearingId
    dst: ClearingId
    count: int


@dataclass(frozen=True)
class Recruit:
    clearing: ClearingId
    count: int


@dataclass(frozen=True)
class Score:
    points: int
    reason: str
