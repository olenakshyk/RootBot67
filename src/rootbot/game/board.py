# Модель мапи: галявини, масті, сусіди, пріоритети; завантаження з config/maps/autumn.yaml.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rootbot.common.errors import FatalError
from rootbot.common.types import BoardPoint, ClearingId, Suit
from rootbot.config import ROOT

NUM_CLEARINGS = 12
NUM_CORNERS = 4
CLEARING_SUITS = (Suit.FOX, Suit.RABBIT, Suit.MOUSE)


class BoardError(ValueError):
    """Файл мапи заповнено неправильно. У повідомленні перелічено всі знайдені проблеми."""


@dataclass(frozen=True)
class Clearing:
    id: ClearingId
    suit: Suit
    priority: int  # 1..12, порядок обходу галявин Механічною Маркізою
    corner: bool
    adjacent: tuple[ClearingId, ...]
    center: BoardPoint  # мм, система координат дошки
    radius_mm: float


@dataclass(frozen=True)
class Board:
    clearings: Mapping[ClearingId, Clearing]

    def __getitem__(self, clearing_id: ClearingId) -> Clearing:
        return self.clearings[clearing_id]

    def adjacent(self, clearing_id: ClearingId) -> tuple[ClearingId, ...]:
        return self.clearings[clearing_id].adjacent

    def by_priority(self, suit: Suit | None = None) -> list[Clearing]:
        """Галявини в порядку пріоритету: 1, 2, …, 12. Якщо задано `suit`, лише цієї масті."""
        return sorted(
            (c for c in self.clearings.values() if suit is None or c.suit == suit),
            key=lambda c: c.priority,
        )

    def corners(self) -> list[Clearing]:
        return [c for c in self.clearings.values() if c.corner]


def load_board(path: str | Path) -> Board:
    """Читає YAML-файл мапи й перевіряє його. Кидає BoardError, якщо дані неправильні."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return parse_board(data)


def parse_board(data: Any) -> Board:
    """Будує Board з уже прочитаного YAML (словник з ключем `clearings`)."""
    raw = data.get("clearings") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise BoardError("map file must contain a 'clearings' list")

    errors: list[str] = []
    clearings: dict[ClearingId, Clearing] = {}
    for i, entry in enumerate(raw):
        clearing = _parse_clearing(entry, f"clearings[{i}]", errors)
        if clearing is None:
            continue
        if clearing.id in clearings:
            errors.append(f"clearing {clearing.id}: duplicate id")
            continue
        clearings[clearing.id] = clearing

    errors += _validate(clearings, len(raw))
    if errors:
        raise BoardError("invalid map:\n  " + "\n  ".join(errors))
    return Board(clearings)


def _parse_clearing(entry: Any, where: str, errors: list[str]) -> Clearing | None:
    if not isinstance(entry, dict):
        errors.append(f"{where}: expected a mapping")
        return None
    cid = entry.get("id")
    if not _is_int(cid):
        errors.append(f"{where}: 'id' must be an integer, got {cid!r}")
        return None
    where = f"clearing {cid}"
    n_errors = len(errors)

    suit = None
    try:
        suit = Suit(entry.get("suit"))
    except ValueError:
        pass
    if suit not in CLEARING_SUITS:
        names = " | ".join(s.value for s in CLEARING_SUITS)
        errors.append(f"{where}: 'suit' must be {names}, got {entry.get('suit')!r}")

    priority = entry.get("priority")
    if not _is_int(priority):
        errors.append(f"{where}: 'priority' must be an integer, got {priority!r}")

    corner = entry.get("corner")
    if not isinstance(corner, bool):
        errors.append(f"{where}: 'corner' must be true or false, got {corner!r}")

    adjacent = entry.get("adjacent")
    if not isinstance(adjacent, list) or not all(_is_int(a) for a in adjacent):
        errors.append(f"{where}: 'adjacent' must be a list of clearing ids, got {adjacent!r}")

    center = entry.get("center_mm")
    if not (isinstance(center, list) and len(center) == 2 and all(_is_number(v) for v in center)):
        errors.append(f"{where}: 'center_mm' must be [x, y] in mm, got {center!r}")

    radius = entry.get("radius_mm")
    if not (_is_number(radius) and radius > 0):
        errors.append(f"{where}: 'radius_mm' must be a positive number, got {radius!r}")

    if len(errors) > n_errors:
        return None
    return Clearing(
        id=ClearingId(cid),
        suit=suit,
        priority=priority,
        corner=corner,
        adjacent=tuple(ClearingId(a) for a in adjacent),
        center=BoardPoint(float(center[0]), float(center[1])),
        radius_mm=float(radius),
    )


def _validate(clearings: dict[ClearingId, Clearing], n_entries: int) -> list[str]:
    errors: list[str] = []
    expected_ids = set(range(1, NUM_CLEARINGS + 1))

    if n_entries != NUM_CLEARINGS:
        errors.append(f"expected {NUM_CLEARINGS} clearings, got {n_entries}")
    if unknown := sorted(set(clearings) - expected_ids):
        errors.append(f"clearing ids must be 1..{NUM_CLEARINGS}, got {unknown}")

    priorities = sorted(c.priority for c in clearings.values())
    if len(clearings) == NUM_CLEARINGS and priorities != list(range(1, NUM_CLEARINGS + 1)):
        errors.append(f"priorities must be unique 1..{NUM_CLEARINGS}, got {priorities}")

    corners = sorted(c.id for c in clearings.values() if c.corner)
    if len(corners) != NUM_CORNERS:
        errors.append(f"expected {NUM_CORNERS} corner clearings, got {corners}")

    for c in clearings.values():
        if c.id in c.adjacent:
            errors.append(f"clearing {c.id}: adjacent to itself")
        if len(set(c.adjacent)) != len(c.adjacent):
            errors.append(f"clearing {c.id}: duplicate ids in 'adjacent'")
        for other in c.adjacent:
            if other not in clearings:
                errors.append(f"clearing {c.id}: adjacent to unknown clearing {other}")
            elif c.id not in clearings[other].adjacent:
                errors.append(f"clearing {c.id} -> {other} is not symmetric")

    if clearings and not errors and not _is_connected(clearings):
        errors.append("clearings are not all connected by paths")
    return errors


def _is_connected(clearings: dict[ClearingId, Clearing]) -> bool:
    start = next(iter(clearings))
    seen = {start}
    stack = [start]
    while stack:
        for other in clearings[stack.pop()].adjacent:
            if other not in seen:
                seen.add(other)
                stack.append(other)
    return len(seen) == len(clearings)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def create_board(cfg: dict) -> Board:
    """Мапа з config board.map_file; якщо не читається — FatalError (програма не стартує)."""
    try:
        return load_board(ROOT / cfg["board"]["map_file"])
    except (OSError, yaml.YAMLError, BoardError) as e:
        raise FatalError(f"board.map_file: cannot load the map: {e}") from e
