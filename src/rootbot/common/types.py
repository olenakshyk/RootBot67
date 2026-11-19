# Спільні типи: масті, фракції, ID галявин, кількість фігурок, карта наказу, точка на дошці (мм).
# Це контракт між модулями (docs/architecture.md#ключові-інтерфейси): змінюємо лише через PR,
# який переглядають власники всіх модулів, що їх використовують.
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType

ClearingId = NewType("ClearingId", int)  # номер галявини на мапі, 1..12


class Suit(Enum):
    FOX = "fox"
    RABBIT = "rabbit"
    MOUSE = "mouse"
    BIRD = "bird"  # лише в картах, галявин такої масті немає
    ANY = "any"  # «?» у вартості крафту: символ будь-якої масті; масті карти такої немає


class Faction(Enum):
    MARQUISE_BOT = "marquise_bot"  # бот (Механічна Маркіза) грає фігурками Маркізи де Кіт
    EYRIE = "eyrie"
    ALLIANCE = "alliance"
    VAGABOND = "vagabond"
    RIVERFOLK = "riverfolk"
    LIZARDS = "lizards"


@dataclass
class PieceCounts:
    """Фігурки однієї фракції на одній галявині."""

    warriors: int = 0  # панування = warriors + buildings,
    buildings: int = 0  # «найбільше ворожих фігурок» = усе разом
    tokens: int = 0
    pawn: int = 0  # Бродяга (Vagabond)


@dataclass(frozen=True)
class OrderCard:
    suit: Suit
    craft_cost: tuple[Suit, ...]  # порожній кортеж означає «немає вартості крафту»


@dataclass(frozen=True)
class BoardPoint:
    """Точка в системі координат дошки, мм (docs/architecture.md#системи-координат)."""

    x: float
    y: float
