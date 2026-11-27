# Спільні типи: масті, фракції, вид фігурки, ID галявин, кількість фігурок, карта наказу,
# точка на дошці (мм).
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


class PieceKind(Enum):
    """Вид фігурки, який розпізнає зір."""

    WARRIOR = "warrior"
    BUILDING = "building"
    TOKEN = "token"
    PAWN = "pawn"  # фігурка Бродяги
    KEEP = "keep"  # Фортеця котів (жетон)
    SCORE = "score"  # маркер на доріжці рахунку
    DIE = "die"


@dataclass
class PieceCounts:
    """Фігурки однієї фракції на одній галявині."""

    warriors: int = 0  # панування = warriors + buildings,
    buildings: int = 0  # «найбільше ворожих фігурок» = усе разом
    tokens: int = 0
    pawn: int = 0  # Бродяга (Vagabond)


CARD_SUITS = (Suit.FOX, Suit.RABBIT, Suit.MOUSE, Suit.BIRD)  # масть карти
CRAFT_SUITS = (Suit.FOX, Suit.RABBIT, Suit.MOUSE, Suit.ANY)  # символи вартості крафту


@dataclass(frozen=True)
class OrderCard:
    suit: Suit
    craft_cost: tuple[Suit, ...]  # порожній кортеж означає «немає вартості крафту»

    def __post_init__(self) -> None:
        if self.suit not in CARD_SUITS:
            raise ValueError(f"card suit must be one of {[s.value for s in CARD_SUITS]}")
        if any(s not in CRAFT_SUITS for s in self.craft_cost):
            raise ValueError(f"craft cost symbols must be in {[s.value for s in CRAFT_SUITS]}")


@dataclass(frozen=True)
class BoardPoint:
    """Точка в системі координат дошки, мм (docs/architecture.md#системи-координат)."""

    x: float
    y: float
