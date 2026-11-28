# Архітектура

## Принципи

1. **Кожен модуль можна розробляти без інших.** `game` не знає про камеру й робота. `robot` не знає правил гри.
   `vision` не знає, що робить бот. Їх з'єднує лише `app`.
2. **Без заліза теж працює.** Для камери є режим «читати фото з диска», для робота — `MockArm`.
   Ігрову логіку тестуємо звичайним `pytest` на CI.
3. **Камера — джерело істини про дошку.** Перед кожним ходом бота стан фігурок будується з кадру заново.
   Програма пам'ятає тільки те, чого не видно: рахунок, Keep, пріоритети.
4. **Людина завжди може виправити.** Кожен результат розпізнавання (стан дошки, карта наказу) оператор
   підтверджує або правит у UI. Помилка зору не повинна зупиняти гру.
5. **Безпека важливіша за швидкість.** Робот рухається лише в хід бота, лише після підтвердження оператора,
   з обмеженою швидкістю. Див. [hardware-and-safety.md](hardware-and-safety.md).

## Модулі

```mermaid
flowchart TB
    subgraph app [rootbot.app]
        LOOP[turn_loop<br/>стан-машина ходу]
        UI[operator_ui]
    end
    subgraph game [rootbot.game]
        BOARD[board<br/>мапа: галявини, сусіди]
        STATE[state<br/>GameState]
        BOT[marquise_bot<br/>правила → дії]
    end
    subgraph vision [rootbot.vision]
        CAMV[camera]
        BD[board_detector<br/>малюнок дошки → гомографія]
        PD[piece_detector]
        CR[card_reader]
    end
    subgraph robot [rootbot.robot]
        ARM[arm<br/>XArm / MockArm]
        TF[transforms<br/>дошка мм → база робота]
        PP[pick_place]
    end
    common[rootbot.common<br/>Suit, Faction, ClearingId, ...]

    LOOP --> BOT
    LOOP --> PD
    LOOP --> CR
    LOOP --> PP
    LOOP <--> UI
    PD --> BD --> CAMV
    PP --> TF
    PP --> ARM
    BOT --> STATE --> BOARD
```

| Модуль | Відповідальність | Вхід | Вихід | Залежності |
| --- | --- | --- | --- | --- |
| `common` | Спільні типи й константи | — | — | — |
| `game` | Модель мапи, стан партії, правила Механічної Маркізи | `GameState`, карта наказу | список `Action` | `common` |
| `vision` | Кадр → стан дошки й карта наказу | кадр камери | `BoardObservation`, `OrderCard` | `common`, OpenCV |
| `robot` | Фізичні переміщення фігурок | `PickPlaceTask` (координати дошки, мм) | рух руки | `common`, xArm SDK |
| `app` | Оркестрація ходу, UI оператора, кубики | усе вище | — | усі модулі |

## Ключові інтерфейси

Ці типи — «контракт» між людьми в команді. Змінюємо їх тільки через PR, на який дивляться власники обох
модулів. Точні визначення з'являться в коді (`src/rootbot/...`), тут подано суть.

```python
# common: спільні типи
@dataclass
class PieceCounts:  # фігурки однієї фракції на одній галявині
    warriors: int  # панування = warriors + buildings,
    buildings: int  # «найбільше ворожих фігурок» = усе разом
    tokens: int
    pawn: int  # Бродяга (Vagabond)


@dataclass
class OrderCard:
    suit: Suit  # FOX / RABBIT / MOUSE / BIRD
    # FOX / RABBIT / MOUSE / ANY («?», будь-яка масть); порожній кортеж означає «немає вартості крафту»
    craft_cost: tuple[Suit, ...]


# vision → app
@dataclass
class BoardObservation:
    pieces: dict[ClearingId, dict[Faction, PieceCounts]]  # хто що має на кожній галявині
    keep_clearing: ClearingId | None  # де лежить Keep (None, якщо не знайдено)
    # де саме стоять воїни бота (для захвату)
    bot_piece_positions: dict[ClearingId, list[BoardPoint]]
    # усі зайняті точки галявини: фігурки будь-якої фракції й виду, разом з Keep
    occupied: dict[ClearingId, list[BoardPoint]]
    free_spots: dict[ClearingId, list[BoardPoint]]  # куди можна поставити воїна
    confidence: float  # 0..1, якщо низька, UI просить перевірити
    board_pose: BoardPose | None  # де дошка в системі робота за цим оглядом (None — не вдалося оцінити)


# game → app
Action = Battle | Move | Recruit | Score


@dataclass
class Battle:
    clearing: ClearingId
    defender: Faction


@dataclass
class BattleResult:  # resolve_battle(state, battle, rolls) після кидка кубиків
    bot_warriors_lost: int
    keep_lost: bool
    defender_hits: int  # скільки фігурок знімає захисник (не більше, ніж має); у Бродяги це предмети


@dataclass
class Move:
    src: ClearingId
    dst: ClearingId
    count: int


@dataclass
class Recruit:
    clearing: ClearingId
    count: int


@dataclass
class Score:  # Світанок завжди дає рівно один Score, навіть з 0 ПО
    points: int
    reason: str


# app → robot
@dataclass
class PickPlaceTask:
    pick: BoardPoint  # мм у системі дошки, або SUPPLY
    place: BoardPoint  # мм у системі дошки, або SUPPLY
```

## Цикл ходу бота

```mermaid
sequenceDiagram
    actor Op as Оператор
    participant App as app.turn_loop
    participant Vis as vision
    participant Game as game
    participant Rob as robot

    Op->>App: «Хід бота»
    App->>Vis: capture + detect
    Vis-->>App: BoardObservation
    App->>Op: показати стан, підтвердити / виправити
    Op->>App: OK
    App->>Game: Birdsong(state)
    Game-->>App: Score
    Op->>App: відкрив ліву карту
    App->>Vis: read_card
    Vis-->>App: OrderCard (оператор підтверджує)
    App->>Game: Daylight(state, card)
    loop кожен Battle
        App->>Op: кубики (RNG або ввести), мечі Бродяги
        App->>Game: resolve_battle(state, battle, rolls)
        Game-->>App: BattleResult
        App->>Rob: зняти втрати бота
    end
    opt були бої
        Rob->>Rob: home
        App->>Op: що зняти кожній фракції людей
        Op->>App: зняли, «Хід бота»
        App->>Vis: огляд галявин боїв
    end
    loop кожна Move / Recruit
        App->>Rob: PickPlaceTask × N
        App->>Vis: перевірка результату
    end
    Rob->>Rob: home
    App->>Op: Evening: добрати карти до 5, хід людей
```

Важливо: **дії `Move` залежать від результатів попередніх `Battle`.** Тому `game` не видає всі дії на хід
одразу. `app` викликає рушій крок за кроком (бій → оновити стан → рух → оновити стан → набір), і кожен
крок отримує актуальний `GameState`. Якщо відкрита карта без вартості крафту (`recruits` повертає `None`), після руху
`app` просить відкрити наступну карту й повторює День з нею; якщо карт немає, переходить до Вечора.

## Системи координат

| Система | Одиниці | Початок | Хто використовує |
| --- | --- | --- | --- |
| **Зображення** | пікселі | лівий верхній кут кадру | `vision` всередині |
| **Дошка** (`board`) | мм | лівий верхній кут дошки, коли лого ROOT праворуч угорі; X уздовж довгої сторони, Y донизу (до доріжки рахунку) | **спільна мова** між `vision`, `game` (координати галявин) і `robot` |
| **Робот** (`base`) | мм | основа xArm 7 | `robot` всередині |

- **Зображення → Дошка:** гомографія за малюнком самої дошки (ключові точки ↔ еталонне зображення, без маркерів),
  рахується **на кожному кадрі**, тож якщо дошку штовхнули, підрахунок фігурок по галявинах не ламається.
- **Дошка → Робот:** поза дошки в системі робота (`BoardPose`), яку `vision` рахує **на кожному огляді**:
  поза фланця руки в мить знімка × hand-eye × поза дошки відносно камери (`solvePnP` за тими самими точками).
  Дошку зсунули — наступний огляд дає нову позу, і рука бере фігурки вже за нею. Один раз калібрується лише
  hand-eye (камера відносно фланця). Деталі й де що житиме: [vision.md → V1](vision.md#v1-дошка-й-планшет-бота).

Деталі: [calibration.md](calibration.md).

## Конфігурація

Усі «магічні числа» живуть у `config/`, не в коді:

- `config/default.yaml`: IP робота, швидкості, безпечні висоти, індекс камери, шляхи до файлів калібрування;
- `config/maps/autumn.yaml`: галявини, масті, сусіди, пріоритети, координати центрів;
- `config/calibration/*.yaml`: результати калібрування (генеруються скриптами, **у git не комітимо**,
  бо в кожного своє робоче місце).
