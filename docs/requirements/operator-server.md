# Вимоги: OperatorServer

Бекенд для застосунку оператора ([ADR 0006](../decisions/0006-operator-app.md),
[architecture.md → app](../architecture.md#app--точка-входу-й-оркестратор-партії)).
Код: [`app/operator_ui.py`](../../src/rootbot/app/operator_ui.py) (сигнали й стан),
[`app/operator_api.py`](../../src/rootbot/app/operator_api.py) (HTTP API),
[`app/static/`](../../src/rootbot/app/static/) (сам застосунок: Preact + htm без збірки). Хто ці сигнали обробляє: [orchestrator.md](orchestrator.md).

Застосунок — майже пульт керування ботом. Що автономніший робот, то менше сигналів потрібно; поки сервер мусить бути.
Що з цього має перейти до робота: [autonomy.md](../autonomy.md).

## Методи

Для застосунку (їх викликають обробники HTTP, див. [HTTP API](#http-api)):

| Метод | Сигнал | Навіщо | Автономно |
| --- | --- | --- | --- |
| `your_turn()` | `YourTurn` | почати хід бота; під час ходу — «я допоміг, продовжуй» | [AUT-4](../autonomy.md#aut-4), [AUT-7](../autonomy.md#aut-7) |
| `end_game()` | `EndGame` | кінець партії | [AUT-6](../autonomy.md#aut-6) |
| `new_game()` | `NewGame` | «Play again» після кінця гри: нова партія без перезапуску програми | — |
| `card_given()` | `CardGiven` | гравець мав би дати боту карту → бот отримує ПО | [AUT-5](../autonomy.md#aut-5) |
| `keep_removed()` | `KeepRemoved` | Keep знищено під час ходу людини: бот забуває, де він був | [AUT-13](../autonomy.md#aut-13) |
| `order_card(card)` | `OrderCardRevealed` | відкрита карта наказу: масть + вартість крафту | [AUT-1](../autonomy.md#aut-1) |
| `dice_rolled(rolls, vagabond_swords)` | `DiceRolled` | результат кидка кубиків бою | [AUT-2](../autonomy.md#aut-2), [AUT-3](../autonomy.md#aut-3) |
| `current_state()` | — | поточний стан бота: крок, чого чекає, рахунок, повідомлення людям, помилка | — |

Для `Orchestrator`: `next_signal()` — наступний сигнал без очікування, `set_status(...)` — оновити стан.

## HTTP API

FastAPI + uvicorn у фоновому потоці програми (`main` запускає його після створення `OperatorServer` і зупиняє
при завершенні). Застосунок: `http://<ноутбук>:8000/`; опис API для перевірки руками: `http://<ноутбук>:8000/docs`
(Swagger UI від FastAPI). Шляхи API мають перевагу над файлами застосунку.

| Шлях | Метод | Тіло (JSON) | Відповідь |
| --- | --- | --- | --- |
| `POST /signals/your-turn` | `your_turn()` | — | `202` |
| `POST /signals/end-game` | `end_game()` | — | `202` |
| `POST /signals/new-game` | `new_game()` | — | `202` |
| `POST /signals/card-given` | `card_given()` | — | `202` |
| `POST /signals/keep-removed` | `keep_removed()` | — | `202` |
| `POST /signals/order-card` | `order_card(card)` | `{"suit": "fox", "craft_cost": ["mouse", "any"]}` | `202` |
| `POST /signals/dice` | `dice_rolled(rolls, vagabond_swords)` | `{"rolls": [3, 0], "vagabond_swords": null}` | `202` |
| `GET /state` | `current_state()` | — | `200`, стан (нижче) |
| `GET /` | — | — | застосунок оператора (`app/static/`) |

- `suit`: `fox` / `rabbit` / `mouse` / `bird`; `craft_cost`: значення з `fox` / `rabbit` / `mouse` / `any`
  («?», зокрема 4 × `any`), порожній чи відсутній — немає вартості крафту.
- `rolls`: два кубики `0..3`, порядок неважливий; `vagabond_swords` (≥ 0) лише якщо захищається Бродяга.
  Застосунок кидає їх сам (`crypto.getRandomValues`, рівномірно 0..3, як 12-гранний кубик із трьома гранями на
  значення) і не дає перекинути; API приймає й кидок, зроблений деінде.
- Хибне тіло → `422`, сигнал у чергу не потрапляє.
- `202`: сигнал прийнято в чергу; що з ним зробив бот, видно з `GET /state`.

Відповідь `GET /state`:

```json
{
  "step": "idle",
  "waiting_for": "your_turn",
  "scores": {"marquise_bot": 7, "eyrie": 3},
  "message": "",
  "error": null,
  "battle": null,
  "keep_destroyed": false,
  "winner": null,
  "notice": null,
  "cards_left": null
}
```

Ключі `scores` — значення `Faction` (`marquise_bot`, `eyrie`, `alliance`, `vagabond`).
`battle` — бій, для якого бот чекає кубики (`waiting_for: "dice"`), напр. `{"clearing": 3, "defender": "vagabond"}`:
застосунок показує, з ким бій, і питає мечі Бродяги лише тоді, коли захищається він. Поза боєм `null`.
`keep_destroyed` — Keep знищено (сигнал `keep-removed` або бій, де бот його втратив). Keep не відбудовують,
тож застосунок більше не показує кнопку «Keep destroyed».
`winner` — фракція, що набрала `game.win_points` (бот виграє одразу, щойно набрав; люди — коли бот побачить це
на початку свого ходу). `null`, поки гра йде або якщо її завершив оператор.
`notice` — повідомлення бота людям, поки він чекає `seconds` секунд (`orchestrator.notice_s`). Зараз одне:
після Світанку `{"kind": "birdsong", "points": 4, "clearings": [1, 5], "seconds": 4}` — скільки ПО бот собі нарахував
і за які галявини; потім бот сам переходить до Дня.
`cards_left` — поки бот чекає карту наказу: скільки карт Розкладу в цьому ході ще не відкрито (на початку Дня
`game.evening.schedule_size`). Якщо попередня карта була без вартості крафту, День починається заново:
`step` тоді `"daylight: no craft cost, reveal the next order card"`, і застосунок пояснює, чому знову просить карту.
Коли карт не лишилося, бот іде до Вечора, не питаючи.

Конфігурація: `config/default.yaml` → `operator_server`: `host` (типово `0.0.0.0`, щоб застосунок на телефоні в
тій самій Wi-Fi мережі бачив сервер), `port` (`8000`; `0` — будь-який вільний, для тестів), `cors_origins`
(вебсторінки, яким браузер дозволить звертатися до API), `start_timeout_s`. Порт зайнятий → `TemporaryError`,
`main` повторює спробу (`startup.retries`).

Залежності: `fastapi`, `uvicorn` (основні), `httpx` (dev, для `TestClient` у тестах).

## Функціональні вимоги (FR)

| ID | Вимога |
| --- | --- |
| FR-S1 | Приймає сигнали з таблиці вище й передає їх `Orchestrator` у порядку надходження. |
| FR-S2 | На запит віддає поточний стан (`BotStatus`): `step`, `waiting_for` (`your_turn` / `order_card` / `dice`), `scores`, `message`, `error`, `battle`, `keep_destroyed`, `winner`, `notice`, `cards_left`. |
| FR-S3 | `waiting_for` підказує застосунку, яку кнопку чи форму показати зараз. |
| FR-S4 | Можлива фізична червона кнопка (ESP32), що надсилає ті самі сигнали ([ADR 0006](../decisions/0006-operator-app.md)). |

## Нефункціональні вимоги (NFR)

| ID | Вимога |
| --- | --- |
| NFR-S1 | FastAPI + uvicorn у фоновому потоці тієї самої програми, що й `Orchestrator`. |
| NFR-S2 | Сигнали — через `queue.Queue`; стан — спільний об'єкт під `threading.Lock`. |
| NFR-S3 | Сервер не викликає `game`, `vision`, `robot`: лише передає сигнали й віддає стан. |
| NFR-S4 | Аварійна зупинка (E-stop) — апаратна, не через сервер. |

## Застосунок (`app/static/`)

Одна сторінка, екран вибирається за станом бота (`GET /state` раз на секунду):

| Стан | Екран | Що робить людина |
| --- | --- | --- |
| `step: "starting"` | бот готується | нічого |
| `waiting_for: "your_turn"`, без `error` | хід людей | «Start bot turn» → `your-turn` |
| `waiting_for: "your_turn"` з `error` | потрібна допомога (AUT-7) | доробляє крок руками, «Continue the turn» → `your-turn` |
| `notice` (`kind: "birdsong"`) | повідомлення бота: «оглянув дошку, нарахував N ПО» і смужка до наступного кроку | читає |
| `waiting_for: "order_card"` | карта наказу | масть і вартість крафту → `order-card` |
| `waiting_for: "dice"` | бій | «Roll the dice»: застосунок сам кидає два кубики (анімація) й надсилає `dice`; для Бродяги спершу вибрати його мечі |
| `waiting_for: null` | бот ходить | тримає руки подалі від дошки |
| `step: "game over"`, `waiting_for: "new_game"` | кінець гри: переможець, фінальний рахунок | розставляє дошку заново, «Play again» → `new-game` |

На всіх екранах, крім кінця гри: рахунок, `message` («For the players») і «Other signals»: «Card given to bot»
(`card-given`), «Keep destroyed» (`keep-removed`; зникає, щойно Keep знищено), «End game» (`end-game`, з підтвердженням). Після сигналу кнопка
неактивна, поки стан бота не зміниться (до 8 с), щоб не надіслати двічі. Без зв'язку сторінка показує останній стан
і не дає надсилати. Інтерфейс англійською; шрифти лежать у `app/static/vendor/fonts/`.
Небо на тлі показує пору доби в грі (`app/static/sky.js`): Світанок — ранок, День (зокрема карта наказу й бій) —
день, Вечір — вечір, хід людей, підготовка й кінець гри — ніч. Кольори плавно перетікають, сонце й місяць ідуть
однією дугою (CSS `@property` і `sin()`/`cos()`; у старих браузерах просто без анімації).

Без руки й камери: `python scripts/serve_operator_app.py [--fail] [--vagabond] [--score N]`, несправжній бот проходить
усі екрани (`--fail`: збій під час набору, `--vagabond`: захищається Бродяга, `--score 27`: швидко до перемоги).
