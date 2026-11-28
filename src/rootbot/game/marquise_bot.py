"""Бот Механічна Маркіза: Світанок, День (бій / рух / набір), Вечір.

Зовнішній опис: docs/architecture.md -> game -> marquise_bot; правила: docs/game-rules.md.

День видається крок за кроком (бій -> рух -> набір), бо рух залежить від результату бою:
Orchestrator після кожного кроку передає оновлений GameState.

Тести: правила детерміновані (той самий стан і та сама карта наказу дають ті самі дії),
тож кожен приклад із правил і кожен знайдений баг оформлюємо як сценарій у
tests/game/scenarios/: стан дошки + карта наказу (YAML) -> очікуваний список дій.
"""

from __future__ import annotations

from dataclasses import replace

from rootbot.common.types import Faction, OrderCard, PieceCounts, Suit
from rootbot.game.actions import Battle, BattleResult, Move, Recruit, Score
from rootbot.game.board import CLEARING_SUITS, Board, Clearing
from rootbot.game.state import BOT, GameState

DIE_FACES = range(4)  # 12-гранний кубик бою має значення 0..3


def birdsong(state: GameState, *, points_per_clearing: int, min_warriors: int) -> Score:
    """Світанок: `points_per_clearing` ПО за кожну галявину, якою бот панує і де в нього не менше
    `min_warriors` воїнів. Числа беруться з `game.birdsong` у config/default.yaml.

    Завжди повертає один `Score` (можливо, з 0 ПО); у `reason` галявини в порядку пріоритету.
    """
    scoring = [
        c.id
        for c in state.board.by_priority()
        if state.ruling_faction(c.id) == BOT and state.counts(c.id, BOT).warriors >= min_warriors
    ]
    clearings = ", ".join(str(c) for c in scoring) or "none"
    return Score(points_per_clearing * len(scoring), f"birdsong: scoring clearings {clearings}")


def battles(state: GameState, card: OrderCard) -> list[Battle]:
    """День, крок 1: бій на кожній галявині масті карти, де є воїни бота й ворожі фігурки.

    Захисника обирає `GameState.defender`. Бої на різних галявинах не впливають один на одний,
    тому видаються всі разом, у порядку пріоритету галявин.
    """
    result = []
    for clearing in order_clearings(state.board, card):
        defender = state.defender(clearing.id)
        if state.counts(clearing.id, BOT).warriors > 0 and defender is not None:
            result.append(Battle(clearing.id, defender))
    return result


def resolve_battle(
    state: GameState,
    battle: Battle,
    rolls: tuple[int, int],
    *,
    vagabond_swords: int | None = None,
) -> BattleResult:
    """Наслідки бою бота за кидком двох кубиків. Бот завжди атакує.

    - Атакувальник отримує більше значення, захисник менше. Альянс як захисник
      навпаки отримує більше.
    - Влучань від кубика не більше, ніж воїнів сторони на галявині. У Бродяги замість воїнів
      рахуються непошкоджені мечі `vagabond_swords`: їх обов'язково передати, якщо захищається він.
    - Захисник без воїнів (Бродяга без мечів) беззахисний і отримує +1 влучання.
    - Бот знімає спершу воїнів, а Keep лише тоді, коли воїнів на галявині вже немає.
    - Влучань по захиснику не більше, ніж у нього фігурок на галявині (зайві пропадають).
      Бродягу не обрізаємо: влучання по ньому йдуть у предмети, яких камера не бачить.
    """
    if len(rolls) != 2 or any(r not in DIE_FACES for r in rolls):
        raise ValueError(f"rolls must be two values in 0..{DIE_FACES[-1]}, got {rolls}")
    enemy = state.counts(battle.clearing, battle.defender)
    if battle.defender is Faction.VAGABOND:
        if vagabond_swords is None or vagabond_swords < 0:
            raise ValueError("vagabond_swords must be given and non-negative for a Vagabond battle")
        defender_max = vagabond_swords
    else:
        defender_max = enemy.warriors

    high, low = max(rolls), min(rolls)
    bot_roll, defender_roll = (low, high) if battle.defender is Faction.ALLIANCE else (high, low)
    bot_warriors = state.counts(battle.clearing, BOT).warriors

    defender_hits = min(bot_roll, bot_warriors) + (1 if defender_max == 0 else 0)
    if battle.defender is not Faction.VAGABOND:
        defender_hits = min(defender_hits, enemy.warriors + enemy.buildings + enemy.tokens)
    bot_hits = min(defender_roll, defender_max)
    return BattleResult(
        bot_warriors_lost=min(bot_hits, bot_warriors),
        keep_lost=bot_hits > bot_warriors and state.keep_clearing == battle.clearing,
        defender_hits=defender_hits,
    )


def moves(state: GameState, card: OrderCard, *, stay_warriors: int) -> list[Move]:
    """День, крок 2: рух. Галявини масті карти в порядку пріоритету; з кожної, якою бот панує і де
    в нього більше `stay_warriors` воїнів, усі, крім `stay_warriors`, ідуть у сусідню галявину з
    найбільшою кількістю ворожих фігурок (нічия: менший номер пріоритету). Число береться з
    `game.move` у config/default.yaml.

    Умова перевіряється на дошці після попередніх рухів цього ж кроку, тому воїни, які щойно
    прийшли, рушають далі, якщо їхня галявина йде пізніше за пріоритетом. `state` не змінюється.
    """
    board = state.board
    # Копія фігурок, у якій виконуємо рухи по черзі.
    current = replace(
        state,
        pieces={c: {f: replace(n) for f, n in here.items()} for c, here in state.pieces.items()},
    )
    result = []
    for src in (c.id for c in order_clearings(board, card)):
        warriors = current.counts(src, BOT).warriors
        if current.ruling_faction(src) != BOT or warriors <= stay_warriors:
            continue
        dst = max(
            board.adjacent(src),
            key=lambda c: (current.enemy_pieces(c), -board[c].priority),
        )
        count = warriors - stay_warriors
        current.pieces[src][BOT].warriors -= count
        current.pieces.setdefault(dst, {}).setdefault(BOT, PieceCounts()).warriors += count
        result.append(Move(src, dst, count))
    return result


def recruits(state: GameState, card: OrderCard, *, total_warriors: int) -> list[Recruit] | None:
    """День, крок 3: набір за вартістю крафту карти. Скільки воїнів у бота всього, береться з
    `game.recruit` у config/default.yaml.

    - 1–3 символи однієї масті: стільки воїнів, скільки символів, на кожну галявину цієї масті,
      якою бот панує;
    - по одному символу кожної масті: по 1 воїну на кожну галявину, якою бот панує;
    - 4 символи «?» (будь-яка масть): 4 воїни в галявину з Keep (якщо Keep знято, нікуди).

    Якщо в запасі воїнів не вистачає, галявини заповнюються в порядку пріоритету, кожна повністю
    перед наступною; решта пропадає. Порожній список: набирати нікуди або нема кого.

    `None`: у карти немає вартості крафту, тож після бою й руху День починається заново з
    наступною картою Розкладу, а якщо карт не лишилося, бот переходить до Вечора. Це робить `app`.
    Перевіряти треба `is None`, бо порожній список теж хибний.
    """
    cost = card.craft_cost
    if not cost:
        return None
    ruled = [c for c in state.board.by_priority() if state.ruling_faction(c.id) == BOT]
    if cost == (Suit.ANY,) * 4:
        targets = [] if state.keep_clearing is None else [state.keep_clearing]
        per_clearing = len(cost)
    elif len(cost) == 3 and set(cost) == set(CLEARING_SUITS):
        targets = [c.id for c in ruled]
        per_clearing = 1
    elif len(cost) <= 3 and len(set(cost)) == 1 and cost[0] in CLEARING_SUITS:
        targets = [c.id for c in ruled if c.suit == cost[0]]
        per_clearing = len(cost)
    else:
        raise ValueError(f"unsupported craft cost {[s.value for s in cost]}")

    on_board = sum(state.counts(c, BOT).warriors for c in state.board.clearings)
    supply = max(total_warriors - on_board, 0)
    result = []
    for clearing in targets:
        count = min(per_clearing, supply)
        if count == 0:
            break
        result.append(Recruit(clearing, count))
        supply -= count
    return result


def evening(cards_in_schedule: int, *, schedule_size: int) -> int:
    """Вечір: скільки карт оператор добирає праворуч у Розклад, щоб їх стало `schedule_size`.
    Число береться з `game.evening` у config/default.yaml."""
    if not 0 <= cards_in_schedule <= schedule_size:
        raise ValueError(
            f"cards_in_schedule must be in 0..{schedule_size}, got {cards_in_schedule}"
        )
    return schedule_size - cards_in_schedule


def card_given(*, points: int) -> Score:
    """Poor Manual Dexterity: гравець мав би дати боту карту (зокрема Бродяга, коли допомагає боту
    й бере предмет з Items for Sale). Карта скидається, а бот отримує `points` ПО з
    `game.traits` у config/default.yaml.

    Інші риси бота нічого не рахують, їх виконують люди: карту з руки бота замінює верхня карта
    колоди, а засідки (Hates Surprises) проти бота не граються.
    """
    return Score(points, "poor manual dexterity: card given to bot")


def order_clearings(board: Board, card: OrderCard) -> list[Clearing]:
    """Галявини масті карти наказу в порядку пріоритету. Птах діє як будь-яка масть: усі."""
    return board.by_priority(None if card.suit == Suit.BIRD else card.suit)
