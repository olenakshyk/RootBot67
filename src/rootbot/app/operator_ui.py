"""OperatorServer — зв'язок із застосунком оператора (ADR 0006): сигнали туди, статус бота назад.

Зовнішній опис: docs/architecture.md -> app -> OperatorServer;
вимоги: docs/requirements/operator-server.md.

Як влаштовано: FastAPI (uvicorn) працюватиме у фоновому потоці, Orchestrator — у головному.
Сигнали: методи-обробники кладуть їх у queue.Queue (потокобезпечна),
Orchestrator бере next_signal().
Чого бот чекає від людини — WaitingFor у BotStatus.waiting_for (None — нічого, бот зайнятий).
Сервер приймає лише сигнал, якого бот чекає зараз (ACCEPTED_SIGNALS за waiting_for),
і EndGame — будь-коли; інший сигнал відхиляє з SignalRejected.
Прийнявши відповідь на очікування (ANSWERS), сервер одразу ставить waiting_for = None,
тож повторне натискання тієї самої кнопки відхиляється.
Перевірка, черга й скидання waiting_for — одна дія під threading.Lock.
Статус для застосунку: BotStatus під threading.Lock; Orchestrator оновлює set_status()
(лише передані поля), застосунок читає current_state().

Сигнали, які згодом замінить робот, позначені AUT-n: docs/autonomy.md.

Поки є лише ця частина в одній програмі; HTTP (шляхи, конфіг, FastAPI) — пізніше.
Методи для застосунку вже названі так, як їх викликатимуть обробники HTTP.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from rootbot.common.types import Faction, OrderCard, Suit


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
    """Результат кидка двох кубиків бою, кожен 0..die_max. Тимчасово кидає людина (AUT-2, AUT-3)."""

    rolls: tuple[int, int]
    vagabond_swords: int | None = None  # непошкоджені мечі, якщо захищається Бродяга


Signal = YourTurn | EndGame | CardGiven | OrderCardRevealed | DiceRolled


class WaitingFor(Enum):
    """Чого бот чекає від людини; значення йдуть застосунку як є."""

    YOUR_TURN = "your_turn"
    ORDER_CARD = "order_card"
    DICE = "dice"


ACCEPTED_SIGNALS: dict[WaitingFor | None, tuple[type[Signal], ...]] = {
    WaitingFor.YOUR_TURN: (YourTurn, CardGiven),
    WaitingFor.ORDER_CARD: (OrderCardRevealed,),
    WaitingFor.DICE: (DiceRolled,),
}
"""Які сигнали сервер приймає за кожного значення waiting_for; EndGame — за будь-якого."""

ANSWERS: tuple[type[Signal], ...] = (YourTurn, OrderCardRevealed, DiceRolled)
"""Сигнали, що відповідають на waiting_for: прийнявши такий, сервер ставить waiting_for = None."""


class SignalRejected(ValueError):
    """Сигнал прийшов, коли бот чекає іншого; обробник HTTP повертає застосунку помилку."""


_KEEP: Any = object()
"""Значення за замовчуванням у set_status: поле не передали, воно лишається як є."""


@dataclass(frozen=True)
class BotStatus:
    """Що застосунок показує оператору."""

    step: str = "starting"  # що бот робить зараз, підпис для людини
    waiting_for: WaitingFor | None = None  # чого чекає від людини; None — нічого, бот зайнятий
    scores: dict[Faction, int] = field(default_factory=dict)
    message: str = ""  # що зробити людям (зняти фігурки, добрати карти)
    error: str | None = None  # остання помилка, якщо потрібна допомога


class OperatorServer:
    def __init__(self, die_max: int) -> None:
        self._die_max = die_max
        self._signals: queue.Queue[Signal] = queue.Queue()
        self._status = BotStatus()
        self._lock = threading.Lock()

    # --- застосунок -> бот (обробники HTTP) ---

    def your_turn(self) -> None:
        self._accept(YourTurn())

    def end_game(self) -> None:
        self._accept(EndGame())

    def card_given(self) -> None:
        self._accept(CardGiven())

    def order_card(self, card: OrderCard) -> None:
        """Приймає карту наказу: масть не ANY, у вартості крафту немає BIRD.

        Некоректна карта піднімає SignalRejected з описом, що не так.
        """
        if card.suit is Suit.ANY:
            raise SignalRejected("OrderCardRevealed rejected: card suit cannot be ANY")
        if Suit.BIRD in card.craft_cost:
            raise SignalRejected("OrderCardRevealed rejected: craft cost cannot contain BIRD")
        self._accept(OrderCardRevealed(card))

    def dice_rolled(self, rolls: Sequence[int], vagabond_swords: int | None = None) -> None:
        """Приймає кидок: два цілі 0..die_max, мечі Бродяги — None або ціле >= 0.

        Список із JSON стає кортежем. Некоректний кидок піднімає SignalRejected з описом, що не так.
        """
        self._check_dice(rolls, vagabond_swords)
        self._accept(DiceRolled((rolls[0], rolls[1]), vagabond_swords))

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

    def set_status(
        self,
        *,
        step: str = _KEEP,
        waiting_for: WaitingFor | None = _KEEP,
        scores: dict[Faction, int] = _KEEP,
        message: str = _KEEP,
        error: str | None = _KEEP,
    ) -> None:
        """Замінює в стані передані поля; scores зберігає власною копією.

        waiting_for, що не WaitingFor і не None, піднімає TypeError.
        """
        if waiting_for is not _KEEP and waiting_for is not None:
            if not isinstance(waiting_for, WaitingFor):
                raise TypeError(f"waiting_for must be WaitingFor or None, got {waiting_for!r}")
        passed = {
            "step": step,
            "waiting_for": waiting_for,
            "scores": scores,
            "message": message,
            "error": error,
        }
        changes = {name: value for name, value in passed.items() if value is not _KEEP}
        if "scores" in changes:
            changes["scores"] = dict(scores)
        with self._lock:
            self._status = replace(self._status, **changes)

    def _check_dice(self, rolls: Sequence[int], vagabond_swords: int | None) -> None:
        valid_die = range(self._die_max + 1)
        if len(rolls) != 2 or not all(type(r) is int and r in valid_die for r in rolls):
            raise SignalRejected(
                f"DiceRolled rejected: need 2 integers 0..{self._die_max}, got {rolls!r}"
            )
        if vagabond_swords is not None and not (
            type(vagabond_swords) is int and vagabond_swords >= 0
        ):
            raise SignalRejected(
                f"DiceRolled rejected: vagabond_swords must be >= 0, got {vagabond_swords!r}"
            )

    def _accept(self, signal: Signal) -> None:
        """Кладе сигнал у чергу, якщо бот його зараз чекає (ANSWERS ще й скидають waiting_for).

        Інакше піднімає SignalRejected з назвою сигналу й поточним waiting_for.
        """
        with self._lock:
            waiting_for = self._status.waiting_for
            if not isinstance(signal, (EndGame, *ACCEPTED_SIGNALS.get(waiting_for, ()))):
                name = type(signal).__name__
                raise SignalRejected(f"{name} rejected: waiting_for is {waiting_for}")
            if isinstance(signal, ANSWERS):
                self._status = replace(self._status, waiting_for=None)
            self._signals.put(signal)


def create_operator_server(cfg: dict) -> OperatorServer:
    return OperatorServer(die_max=cfg["game"]["die_max"])
