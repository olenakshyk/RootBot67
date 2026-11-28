"""Застосунок оператора без руки й камери: HTTP API + сторінка + несправжній бот.

Для роботи над інтерфейсом (#26). Замість Orchestrator симулятор відповідає на сигнали
тими самими кроками й текстами, що й справжній, і проходить усі екрани по колу:
«Хід бота» -> огляд дошки -> карта наказу -> кубики -> рух, набір -> Вечір -> знову «Хід бота».
Карта без вартості крафту: після набору бот просить наступну карту (День заново), а коли Розклад
скінчився, іде до Вечора.
З --fail набір один раз «ламається» й чекає допомоги (AUT-7), з --vagabond захищається Бродяга
(застосунок питає його мечі). Бот виграє, щойно набере game.win_points; --score N дає йому N ПО
на старті, щоб швидко дійти до кінця гри. «Play again» починає нову партію.

    python scripts/serve_operator_app.py
    python scripts/serve_operator_app.py --port 8001 --delay 1 --fail --vagabond --score 27

Відкрити http://localhost:8000/ (з телефона в тій самій Wi-Fi: http://<IP ноутбука>:8000/).
Хост і порт — з config/default.yaml -> operator_server (local.yaml поверх).
"""

import argparse
import time

from rootbot.app.operator_api import start_operator_api
from rootbot.app.operator_ui import (
    BattleInfo,
    CardGiven,
    DiceRolled,
    EndGame,
    KeepRemoved,
    NewGame,
    Notice,
    OperatorServer,
    OrderCardRevealed,
    YourTurn,
)
from rootbot.common.types import ClearingId, Faction
from rootbot.config import load_config

POLL_S = 0.05
BATTLE_CLEARING = ClearingId(3)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, help="instead of operator_server.port")
    ap.add_argument("--delay", type=float, default=2, help="how long each fake step takes, s")
    ap.add_argument("--fail", action="store_true", help="fail once while recruiting, wait for help")
    ap.add_argument("--vagabond", action="store_true", help="the Vagabond defends in the battle")
    ap.add_argument("--score", type=int, default=0, help="bot's VP at the start of each game")
    args = ap.parse_args()

    cfg = load_config()
    if args.port is not None:
        cfg["operator_server"]["port"] = args.port

    server = OperatorServer()
    win_points = cfg["game"]["win_points"]
    scores: dict[Faction, int] = {}

    def new_scores() -> None:
        scores.clear()
        scores.update({Faction.MARQUISE_BOT: args.score, Faction.EYRIE: 0})
        scores.update({Faction.ALLIANCE: 0, Faction.VAGABOND: 0})

    new_scores()
    defender = Faction.VAGABOND if args.vagabond else Faction.EYRIE

    def status(**changes: object) -> None:
        server.set_status(scores=dict(scores), **changes)

    def step(name: str, **changes: object) -> None:
        """Крок, під час якого бот «рухає руку» й нічого не чекає."""
        status(step=name, waiting_for=None, **changes)
        time.sleep(args.delay)

    schedule_size = cfg["game"]["evening"]["schedule_size"]
    turn = {"cards_used": 0, "card": None}  # карти Розкладу, відкриті в цьому ході

    def recruit_then_next() -> None:
        """Набір; карта без вартості крафту — День заново з наступною картою або Вечір."""
        step("daylight: recruit")
        if not turn["card"].craft_cost and turn["cards_used"] < schedule_size:
            status(
                step="daylight: no craft cost, reveal the next order card",
                waiting_for="order_card",
                cards_left=schedule_size - turn["cards_used"],
            )
            return
        step("evening", message=f"draw {turn['cards_used']} order cards into the Schedule")
        scores[Faction.MARQUISE_BOT] += 1
        status(step="idle", waiting_for="your_turn")

    api = start_operator_api(server, cfg)
    print(f"http://localhost:{api.port}/  (Ctrl+C to stop)")
    time.sleep(args.delay)  # «starting»: бот підключає камеру й руку
    status(step="idle", waiting_for="your_turn")

    failed = False
    try:
        while True:
            signal = server.next_signal()
            if signal is None:
                time.sleep(POLL_S)
                continue
            print(signal)
            now = server.current_state()

            if isinstance(signal, NewGame) and now.step == "game over":
                new_scores()
                failed = False
                server.reset_status()
                time.sleep(args.delay)  # «starting»
                status(step="idle", waiting_for="your_turn")
            elif isinstance(signal, EndGame) and now.step != "game over":
                status(
                    step="game over", waiting_for="new_game", message="", error=None, battle=None
                )
            elif now.step == "game over":
                print("  ignored: game over")
            elif isinstance(signal, CardGiven):
                scores[Faction.MARQUISE_BOT] += 1
                status()
            elif isinstance(signal, KeepRemoved):
                status(keep_destroyed=True)
            elif isinstance(signal, YourTurn) and now.error and now.step.startswith("needs help"):
                status(waiting_for=None, error=None)  # людина допомогла: той самий крок далі
                recruit_then_next()
            elif isinstance(signal, YourTurn) and now.waiting_for == "your_turn":
                step("birdsong: scanning the board", message="", error=None)
                scores[Faction.MARQUISE_BOT] += 4  # Світанок: галявини 1 і 5
                notice_s = cfg["orchestrator"]["notice_s"]
                notice = Notice("birdsong", 4, (ClearingId(1), ClearingId(5)), notice_s)
                status(step="birdsong: scored", notice=notice)
                time.sleep(notice_s)
                turn.update(cards_used=0, card=None)
                status(
                    step="daylight: reveal the left order card",
                    waiting_for="order_card",
                    notice=None,
                    cards_left=schedule_size,
                )
            elif isinstance(signal, OrderCardRevealed) and now.waiting_for == "order_card":
                turn["card"] = signal.card
                turn["cards_used"] += 1
                step("daylight", cards_left=None)
                where = f"battle in clearing {BATTLE_CLEARING} vs {defender.value}"
                status(
                    step=f"{where}: roll the dice",
                    waiting_for="dice",
                    battle=BattleInfo(BATTLE_CLEARING, defender),
                )
            elif isinstance(signal, DiceRolled) and now.waiting_for == "dice":
                hits = max(signal.rolls)
                if signal.vagabond_swords is not None:
                    print(f"  vagabond swords: {signal.vagabond_swords}")
                message = f"{defender.value}: remove {hits} in clearing {BATTLE_CLEARING}"
                step("daylight: battle", message=message, battle=None)
                step("daylight: move")
                if args.fail and not failed:
                    failed = True
                    status(
                        step="needs help: Recruits",
                        waiting_for="your_turn",
                        error="Recruits: arm: grasp failed (fake)",
                    )
                    continue
                recruit_then_next()
            else:
                print(f"  ignored: bot waits for {now.waiting_for}")

            # перемога одразу, як у Orchestrator.add_score
            if (
                scores[Faction.MARQUISE_BOT] >= win_points
                and server.current_state().step != "game over"
            ):
                status(
                    step="game over",
                    waiting_for="new_game",
                    winner=Faction.MARQUISE_BOT,
                    message="",
                    error=None,
                    battle=None,
                )
    except KeyboardInterrupt:
        pass
    finally:
        api.stop()


if __name__ == "__main__":
    main()
