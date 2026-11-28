"""HTTP API застосунку оператора поверх OperatorServer (ADR 0006).

Шляхи й формат: docs/requirements/operator-server.md -> HTTP API.

Обробники лише викликають методи OperatorServer: сигнали йдуть у його чергу, стан читається
з current_state(). Самі обробники нічого не знають про game, vision, robot (NFR-S3).
Той самий сервер роздає й сам застосунок (static/, Preact + htm без збірки) за адресою «/».

start_operator_api() запускає uvicorn у фоновому потоці (daemon), Orchestrator лишається
в головному. Хост, порт і дозволені джерела (CORS) — у config/default.yaml -> operator_server.
"""

from __future__ import annotations

import logging
import threading
import time
from http import HTTPStatus
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rootbot.app.operator_ui import OperatorServer
from rootbot.common.errors import TemporaryError
from rootbot.common.types import OrderCard, Suit
from rootbot.game.marquise_bot import DIE_FACES

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"  # застосунок оператора


class OrderCardIn(BaseModel):
    suit: Suit
    craft_cost: list[Suit] = Field(default_factory=list)


class DiceIn(BaseModel):
    rolls: tuple[int, int]
    vagabond_swords: int | None = Field(default=None, ge=0)


class BattleOut(BaseModel):
    clearing: int
    defender: str  # Faction.value


class NoticeOut(BaseModel):
    kind: str  # "birdsong"
    points: int
    clearings: list[int]
    seconds: float  # скільки показувати, перш ніж бот піде далі


class StateOut(BaseModel):
    step: str
    waiting_for: str | None
    scores: dict[str, int]  # фракція (Faction.value) -> ПО
    message: str
    error: str | None
    battle: BattleOut | None  # бій, для якого чекаємо кубики
    keep_destroyed: bool  # Keep знищено (його не відбудовують)
    winner: str | None  # Faction.value того, хто набрав win_points
    notice: NoticeOut | None  # повідомлення бота людям
    cards_left: int | None  # карт Розкладу ще не відкрито (поки чекаємо карту наказу)


def create_app(server: OperatorServer, cors_origins: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="RootBot operator API", version="1")
    if cors_origins:
        # застосунок у браузері може бути відкритий з іншої адреси, ніж цей сервер
        app.add_middleware(
            CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"]
        )

    accepted = HTTPStatus.ACCEPTED  # сигнал у черзі; що з ним зробив бот — у /state

    @app.post("/signals/your-turn", status_code=accepted)
    def your_turn() -> None:
        server.your_turn()

    @app.post("/signals/end-game", status_code=accepted)
    def end_game() -> None:
        server.end_game()

    @app.post("/signals/new-game", status_code=accepted)
    def new_game() -> None:
        server.new_game()

    @app.post("/signals/card-given", status_code=accepted)
    def card_given() -> None:
        server.card_given()

    @app.post("/signals/keep-removed", status_code=accepted)
    def keep_removed() -> None:
        server.keep_removed()

    @app.post("/signals/order-card", status_code=accepted)
    def order_card(card: OrderCardIn) -> None:
        try:
            order = OrderCard(card.suit, tuple(card.craft_cost))  # перевіряє масті сам
        except ValueError as e:
            raise HTTPException(HTTPStatus.UNPROCESSABLE_ENTITY, str(e)) from e
        server.order_card(order)

    @app.post("/signals/dice", status_code=accepted)
    def dice(roll: DiceIn) -> None:
        if any(r not in DIE_FACES for r in roll.rolls):
            raise HTTPException(
                HTTPStatus.UNPROCESSABLE_ENTITY, f"rolls must be in 0..{DIE_FACES[-1]}"
            )
        server.dice_rolled(roll.rolls, roll.vagabond_swords)

    @app.get("/state")
    def state() -> StateOut:
        now = server.current_state()
        return StateOut(
            step=now.step,
            waiting_for=now.waiting_for,
            scores={faction.value: points for faction, points in now.scores.items()},
            message=now.message,
            error=now.error,
            battle=BattleOut(clearing=now.battle.clearing, defender=now.battle.defender.value)
            if now.battle
            else None,
            keep_destroyed=now.keep_destroyed,
            winner=now.winner.value if now.winner else None,
            notice=NoticeOut(
                kind=now.notice.kind,
                points=now.notice.points,
                clearings=list(now.notice.clearings),
                seconds=now.notice.seconds,
            )
            if now.notice
            else None,
            cards_left=now.cards_left,
        )

    # останнім: шляхи API вище мають перевагу над файлами
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="app")
    return app


class OperatorApi:
    """uvicorn у фоновому потоці. stop() — щоб програма завершилась чисто."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread

    @property
    def port(self) -> int:
        """Справжній порт (важливо, якщо в конфігу port: 0 — будь-який вільний)."""
        return self._server.servers[0].sockets[0].getsockname()[1]

    def stop(self, timeout_s: float = 5) -> None:
        self._server.should_exit = True
        self._thread.join(timeout_s)


def start_operator_api(server: OperatorServer, cfg: dict) -> OperatorApi:
    """Запустити HTTP API й дочекатися, поки він слухає порт.

    Порт зайнятий чи сервер не встиг стартувати -> TemporaryError (main повторить спробу).
    """

    api_cfg = cfg["operator_server"]
    app = create_app(server, api_cfg["cors_origins"])
    # log_config=None: логи uvicorn ідуть у наш логер (main.setup_logging);
    # без access-логу, бо застосунок постійно опитує /state
    config = uvicorn.Config(
        app, host=api_cfg["host"], port=api_cfg["port"], log_config=None, access_log=False
    )
    uv_server = uvicorn.Server(config)

    def run() -> None:
        try:
            uv_server.run()
        except SystemExit:  # uvicorn так завершується, якщо не зміг стартувати (порт зайнятий)
            pass

    thread = threading.Thread(target=run, name="operator-api", daemon=True)
    thread.start()

    deadline = time.monotonic() + api_cfg["start_timeout_s"]
    while not uv_server.started:
        if not thread.is_alive():  # uvicorn завершився сам: найчастіше порт зайнятий
            raise TemporaryError(
                f"operator API did not start on {api_cfg['host']}:{api_cfg['port']} (port in use?)"
            )
        if time.monotonic() > deadline:
            uv_server.should_exit = True
            raise TemporaryError(f"operator API did not start in {api_cfg['start_timeout_s']} s")
        time.sleep(0.01)

    api = OperatorApi(uv_server, thread)
    log.info(f"operator API: http://{api_cfg['host']}:{api.port}")
    return api
