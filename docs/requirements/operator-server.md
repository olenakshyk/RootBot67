# Вимоги: OperatorServer

Бекенд для застосунку оператора ([ADR 0006](../decisions/0006-operator-app.md),
[architecture.md → app](../architecture.md#app--точка-входу-й-оркестратор-партії)).
Код: [`app/operator_ui.py`](../../src/rootbot/app/operator_ui.py). Хто ці сигнали обробляє: [orchestrator.md](orchestrator.md).

Застосунок — майже пульт керування ботом. Що автономніший робот, то менше сигналів потрібно; поки сервер мусить бути.
Що з цього має перейти до робота: [autonomy.md](../autonomy.md).
Шляхи API, конфігурація й залежності сервера ще не визначені.

## Методи

Для застосунку (їх викликатимуть обробники HTTP):

| Метод | Сигнал | Навіщо | Автономно |
| --- | --- | --- | --- |
| `your_turn()` | `YourTurn` | почати хід бота; під час ходу — «я допоміг, продовжуй» | [AUT-4](../autonomy.md#aut-4), [AUT-7](../autonomy.md#aut-7) |
| `end_game()` | `EndGame` | кінець партії | [AUT-6](../autonomy.md#aut-6) |
| `card_given()` | `CardGiven` | гравець мав би дати боту карту → бот отримує ПО | [AUT-5](../autonomy.md#aut-5) |
| `order_card(card)` | `OrderCardRevealed` | відкрита карта наказу: масть + вартість крафту | [AUT-1](../autonomy.md#aut-1) |
| `dice_rolled(rolls, vagabond_swords)` | `DiceRolled` | результат кидка кубиків бою | [AUT-2](../autonomy.md#aut-2), [AUT-3](../autonomy.md#aut-3) |
| `current_state()` | — | поточний стан бота: крок, чого чекає, рахунок, повідомлення людям, помилка | — |

Для `Orchestrator`: `next_signal()` — наступний сигнал без очікування, `set_status(...)` — оновити стан.

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
