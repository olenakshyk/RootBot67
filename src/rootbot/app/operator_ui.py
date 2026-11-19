"""OperatorServer — зв'язок із застосунком оператора (ADR 0006): сигнали туди, статус бота назад.

Зовнішній опис: docs/architecture.md -> app -> OperatorServer;
вимоги: docs/requirements/operator-server.md.

Як влаштовано: FastAPI (uvicorn) працюватиме у фоновому потоці, Orchestrator — у головному.
Сигнали: методи-обробники кладуть їх у queue.Queue (потокобезпечна),
Orchestrator бере next_signal().
Статус для застосунку: BotStatus під threading.Lock; Orchestrator оновлює set_status(),
застосунок читає current_state().

Сигнали, які згодом замінить робот, позначені AUT-n: docs/autonomy.md.

Поки є лише ця частина в одній програмі; HTTP (шляхи, конфіг, FastAPI) — пізніше.
Методи для застосунку вже названі так, як їх викликатимуть обробники HTTP.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field, replace
from typing import Any

from rootbot.common.types import Faction, OrderCard


@dataclass(frozen=True)
class YourTurn:
    """«Хід бота»: почати хід або продовжити після допомоги людини (тимчасово: AUT-4, AUT-7)."""


@dataclass(frozen=True)
class EndGame:
    """Кінець партії (оператор вирішив або хтось переміг; тимчасово: AUT-6)."""


@dataclass(frozen=True)
class CardGiven:
    """Гравець мав би дати боту карту (Poor Manual Dexterity): бот отримує ПО (тимчасово: AUT-5)."""


@dataclass(frozen=True)
class OrderCardRevealed:
    """Відкрита карта наказу. Тимчасово її вводить людина (AUT-1)."""

    card: OrderCard


@dataclass(frozen=True)
class DiceRolled:
    """Результат кидка двох кубиків бою (0..3). Тимчасово кидає людина (AUT-2, AUT-3)."""

    rolls: tuple[int, int]
    vagabond_swords: int | None = None  # непошкоджені мечі, якщо захищається Бродяга


Signal = YourTurn | EndGame | CardGiven | OrderCardRevealed | DiceRolled


@dataclass(frozen=True)
class BotStatus:
    """Що застосунок показує оператору."""

    step: str = "starting"  # що бот робить зараз
    waiting_for: str | None = None  # чого чекає від людини: "your_turn", "order_card", "dice"
    scores: dict[Faction, int] = field(default_factory=dict)
    message: str = ""  # що зробити людям (зняти фігурки, добрати карти)
    error: str | None = None  # остання помилка, якщо потрібна допомога


class OperatorServer:
    def __init__(self) -> None:
        self._signals: queue.Queue[Signal] = queue.Queue()
        self._status = BotStatus()
        self._lock = threading.Lock()

    # --- застосунок -> бот (обробники HTTP) ---

    def your_turn(self) -> None:
        self._signals.put(YourTurn())

    def end_game(self) -> None:
        self._signals.put(EndGame())

    def card_given(self) -> None:
        self._signals.put(CardGiven())

    def order_card(self, card: OrderCard) -> None:
        self._signals.put(OrderCardRevealed(card))

    def dice_rolled(self, rolls: tuple[int, int], vagabond_swords: int | None = None) -> None:
        self._signals.put(DiceRolled(rolls, vagabond_swords))

    def current_state(self) -> BotStatus:
        with self._lock:
            return self._status

    # --- Orchestrator ---

    def next_signal(self) -> Signal | None:
        """Наступний сигнал або None, якщо черга порожня (не чекає)."""
        try:
            return self._signals.get_nowait()
        except queue.Empty:
            return None

    def set_status(self, **changes: Any) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)


def create_operator_server(cfg: dict) -> OperatorServer:
    return OperatorServer()
