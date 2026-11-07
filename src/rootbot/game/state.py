# Стан партії: фігурки на галявинах, Keep, рахунок; панування й вибір захисника.
# Фігурки щоходу перечитуються з камери, решту (рахунок, Keep) програма пам'ятає сама.
# Див. docs/game-rules.md.
from __future__ import annotations

from dataclasses import dataclass, field, fields

from rootbot.common.types import ClearingId, Faction, PieceCounts
from rootbot.game.board import Board

BOT = Faction.MARQUISE_BOT
# Фракції, з якими граємо, у порядку підготовки: літери A, B, C, D на їхніх планшетах.
SETUP_ORDER = (BOT, Faction.EYRIE, Faction.ALLIANCE, Faction.VAGABOND)


class GameStateError(ValueError):
    """Стан партії суперечливий. У повідомленні перелічено всі знайдені проблеми."""


@dataclass
class GameState:
    board: Board
    pieces: dict[ClearingId, dict[Faction, PieceCounts]] = field(default_factory=dict)
    keep_clearing: ClearingId | None = None
    scores: dict[Faction, int] = field(default_factory=dict)  # ПО; кого немає, той має 0

    def __post_init__(self) -> None:
        errors = self._validate()
        if errors:
            raise GameStateError("invalid game state:\n  " + "\n  ".join(errors))

    def counts(self, clearing: ClearingId, faction: Faction) -> PieceCounts:
        """Фігурки фракції на галявині (нулі, якщо їх там немає)."""
        return self._at(clearing).get(faction, PieceCounts())

    def ruling_faction(self, clearing: ClearingId) -> Faction | None:
        """Хто панує галявиною: найбільше воїнів + будівель; нічия означає, що не панує ніхто.

        Виняток: Династія (Lords of the Forest) панує й при нічиїй, якщо має там воїна чи будівлю.
        """
        presence = {f: _presence(c) for f, c in self._at(clearing).items()}
        best = max(presence.values(), default=0)
        if best == 0:
            return None
        leaders = [f for f, n in presence.items() if n == best]
        if len(leaders) == 1:
            return leaders[0]
        if Faction.EYRIE in leaders:
            return Faction.EYRIE
        return None

    def enemies(self, clearing: ClearingId) -> dict[Faction, PieceCounts]:
        """Фігурки суперників бота, у яких є хоч одна фігурка на галявині, у порядку підготовки."""
        here = self._at(clearing)
        return {f: here[f] for f in SETUP_ORDER if f != BOT and f in here and _total(here[f]) > 0}

    def enemy_pieces(self, clearing: ClearingId) -> int:
        """Усі ворожі фігурки на галявині: воїни, будівлі, жетони й пішаки всіх суперників разом."""
        return sum(_total(c) for c in self.enemies(clearing).values())

    def defender(self, clearing: ClearingId) -> Faction | None:
        """З ким бот б'ється на галявині: найбільше фігурок → найбільше ПО → раніше в порядку
        підготовки. None, якщо ворогів немає."""
        enemies = self.enemies(clearing)
        return max(
            enemies,
            key=lambda f: (_total(enemies[f]), self.scores.get(f, 0), -SETUP_ORDER.index(f)),
            default=None,
        )

    def clearings_with(self, faction: Faction, *, warriors_only: bool = False) -> list[ClearingId]:
        """Галявини, де фракція має хоч одну фігурку (чи воїна), у порядку пріоритету."""
        count = (lambda c: c.warriors) if warriors_only else _total
        return [c.id for c in self.board.by_priority() if count(self.counts(c.id, faction)) > 0]

    def _at(self, clearing: ClearingId) -> dict[Faction, PieceCounts]:
        if clearing not in self.board.clearings:
            raise GameStateError(f"unknown clearing {clearing}")
        return self.pieces.get(clearing, {})

    def _validate(self) -> list[str]:
        errors: list[str] = []
        for clearing, by_faction in self.pieces.items():
            if clearing not in self.board.clearings:
                errors.append(f"pieces: unknown clearing {clearing}")
                continue
            for faction, counts in by_faction.items():
                if faction not in SETUP_ORDER:
                    errors.append(f"clearing {clearing}: unsupported faction {faction.value}")
                negative = [f.name for f in fields(counts) if getattr(counts, f.name) < 0]
                if negative:
                    errors.append(f"clearing {clearing}: negative {faction.value} {negative}")

        if self.keep_clearing is not None and self.keep_clearing not in self.board.clearings:
            errors.append(f"keep_clearing: unknown clearing {self.keep_clearing}")
        for faction, score in self.scores.items():
            if faction not in SETUP_ORDER:
                errors.append(f"scores: unsupported faction {faction.value}")
            if score < 0:
                errors.append(f"scores: negative score for {faction.value}")
        return errors


def _presence(counts: PieceCounts) -> int:
    """Те, що рахується для панування: воїни + будівлі."""
    return counts.warriors + counts.buildings


def _total(counts: PieceCounts) -> int:
    """Усі фігурки: воїни, будівлі, жетони, пішак."""
    return counts.warriors + counts.buildings + counts.tokens + counts.pawn
