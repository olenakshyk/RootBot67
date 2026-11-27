# Вимоги: OperatorServer

Бекенд для застосунку оператора ([ADR 0006](../decisions/0006-operator-app.md),
[architecture.md → app](../architecture.md#app--точка-входу-й-оркестратор-партії)).
Код: [`app/operator_ui.py`](../../src/rootbot/app/operator_ui.py) (сигнали й стан),
[`app/operator_api.py`](../../src/rootbot/app/operator_api.py) (HTTP API). Хто ці сигнали обробляє: [orchestrator.md](orchestrator.md).

Застосунок — майже пульт керування ботом. Що автономніший робот, то менше сигналів потрібно; поки сервер мусить бути.
Що з цього має перейти до робота: [autonomy.md](../autonomy.md).

## Методи

Для застосунку (їх викликають обробники HTTP, див. [HTTP API](#http-api)):

| Метод | Сигнал | Навіщо | Автономно |
| --- | --- | --- | --- |
| `your_turn()` | `YourTurn` | почати хід бота; під час ходу — «я допоміг, продовжуй» | [AUT-4](../autonomy.md#aut-4), [AUT-7](../autonomy.md#aut-7) |
| `end_game()` | `EndGame` | кінець партії | [AUT-6](../autonomy.md#aut-6) |
| `card_given()` | `CardGiven` | гравець мав би дати боту карту → бот отримує ПО | [AUT-5](../autonomy.md#aut-5) |
| `keep_removed()` | `KeepRemoved` | Keep знищено під час ходу людини: бот забуває, де він був | [AUT-13](../autonomy.md#aut-13) |
| `order_card(card)` | `OrderCardRevealed` | відкрита карта наказу: масть + вартість крафту | [AUT-1](../autonomy.md#aut-1) |
| `dice_rolled(rolls, vagabond_swords)` | `DiceRolled` | результат кидка кубиків бою | [AUT-2](../autonomy.md#aut-2), [AUT-3](../autonomy.md#aut-3) |
| `current_state()` | — | поточний стан бота: крок, чого чекає, рахунок, повідомлення людям, помилка | — |

Для `Orchestrator`: `next_signal()` — наступний сигнал без очікування, `set_status(...)` — оновити стан.

## HTTP API

FastAPI + uvicorn у фоновому потоці програми (`main` запускає його після створення `OperatorServer` і зупиняє
при завершенні). Опис для перевірки руками: `http://<ноутбук>:8000/docs` (Swagger UI від FastAPI).

| Шлях | Метод | Тіло (JSON) | Відповідь |
| --- | --- | --- | --- |
| `POST /signals/your-turn` | `your_turn()` | — | `202` |
| `POST /signals/end-game` | `end_game()` | — | `202` |
| `POST /signals/card-given` | `card_given()` | — | `202` |
| `POST /signals/keep-removed` | `keep_removed()` | — | `202` |
| `POST /signals/order-card` | `order_card(card)` | `{"suit": "fox", "craft_cost": ["mouse", "any"]}` | `202` |
| `POST /signals/dice` | `dice_rolled(rolls, vagabond_swords)` | `{"rolls": [3, 0], "vagabond_swords": null}` | `202` |
| `GET /state` | `current_state()` | — | `200`, стан (нижче) |

- `suit`: `fox` / `rabbit` / `mouse` / `bird`; `craft_cost`: значення з `fox` / `rabbit` / `mouse` / `any`
  («?», зокрема 4 × `any`), порожній чи відсутній — немає вартості крафту.
- `rolls`: два кубики `0..3`, порядок неважливий; `vagabond_swords` (≥ 0) лише якщо захищається Бродяга.
- Хибне тіло → `422`, сигнал у чергу не потрапляє.
- `202`: сигнал прийнято в чергу; що з ним зробив бот, видно з `GET /state`.

Відповідь `GET /state`:

```json
{
  "step": "idle",
  "waiting_for": "your_turn",
  "scores": {"marquise_bot": 7, "eyrie": 3},
  "message": "",
  "error": null
}
```

Ключі `scores` — значення `Faction` (`marquise_bot`, `eyrie`, `alliance`, `vagabond`).

Конфігурація: `config/default.yaml` → `operator_server`: `host` (типово `0.0.0.0`, щоб застосунок на телефоні в
тій самій Wi-Fi мережі бачив сервер), `port` (`8000`; `0` — будь-який вільний, для тестів), `cors_origins`
(вебсторінки, яким браузер дозволить звертатися до API), `start_timeout_s`. Порт зайнятий → `TemporaryError`,
`main` повторює спробу (`startup.retries`).

Залежності: `fastapi`, `uvicorn` (основні), `httpx` (dev, для `TestClient` у тестах).

## Функціональні вимоги (FR)

| ID | Вимога |
| --- | --- |
| FR-S1 | Приймає сигнали з таблиці вище й передає їх `Orchestrator` у порядку надходження. |
| FR-S2 | На запит віддає поточний стан (`BotStatus`): `step`, `waiting_for` (`your_turn` / `order_card` / `dice`), `scores`, `message`, `error`. |
| FR-S3 | `waiting_for` підказує застосунку, яку кнопку чи форму показати зараз. |
| FR-S4 | Можлива фізична червона кнопка (ESP32), що надсилає ті самі сигнали ([ADR 0006](../decisions/0006-operator-app.md)). |

## Нефункціональні вимоги (NFR)

| ID | Вимога |
| --- | --- |
| NFR-S1 | FastAPI + uvicorn у фоновому потоці тієї самої програми, що й `Orchestrator`. |
| NFR-S2 | Сигнали — через `queue.Queue`; стан — спільний об'єкт під `threading.Lock`. |
| NFR-S3 | Сервер не викликає `game`, `vision`, `robot`: лише передає сигнали й віддає стан. |
| NFR-S4 | Аварійна зупинка (E-stop) — апаратна, не через сервер. |
