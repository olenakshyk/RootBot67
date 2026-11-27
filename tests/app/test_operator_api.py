import json
import socket
import urllib.request

import pytest
from fastapi.testclient import TestClient

from rootbot.app.operator_api import create_app, start_operator_api
from rootbot.app.operator_ui import (
    CardGiven,
    DiceRolled,
    EndGame,
    KeepRemoved,
    OperatorServer,
    OrderCardRevealed,
    YourTurn,
)
from rootbot.common.errors import TemporaryError
from rootbot.common.types import Faction, OrderCard, Suit


@pytest.fixture
def server():
    return OperatorServer()


@pytest.fixture
def client(server):
    return TestClient(create_app(server))


def signals(server):
    out = []
    while (signal := server.next_signal()) is not None:
        out.append(signal)
    return out


def test_simple_signals_in_order(client, server):
    for path in ("your-turn", "card-given", "keep-removed", "end-game"):
        assert client.post(f"/signals/{path}").status_code == 202
    assert signals(server) == [YourTurn(), CardGiven(), KeepRemoved(), EndGame()]


def test_order_card(client, server):
    r = client.post("/signals/order-card", json={"suit": "fox", "craft_cost": ["mouse", "any"]})
    assert r.status_code == 202
    assert signals(server) == [OrderCardRevealed(OrderCard(Suit.FOX, (Suit.MOUSE, Suit.ANY)))]


def test_order_card_four_any(client, server):
    r = client.post("/signals/order-card", json={"suit": "bird", "craft_cost": ["any"] * 4})
    assert r.status_code == 202
    assert signals(server) == [OrderCardRevealed(OrderCard(Suit.BIRD, (Suit.ANY,) * 4))]


def test_order_card_without_craft_cost(client, server):
    assert client.post("/signals/order-card", json={"suit": "bird"}).status_code == 202
    assert signals(server) == [OrderCardRevealed(OrderCard(Suit.BIRD, ()))]


@pytest.mark.parametrize(
    "body",
    [
        {"suit": "any"},  # «?» буває лише у вартості крафту
        {"suit": "cat"},
        {"suit": "fox", "craft_cost": ["bird"]},
        {},
    ],
)
def test_bad_order_card(client, server, body):
    assert client.post("/signals/order-card", json=body).status_code == 422
    assert signals(server) == []


def test_dice(client, server):
    assert client.post("/signals/dice", json={"rolls": [3, 0]}).status_code == 202
    r = client.post("/signals/dice", json={"rolls": [1, 2], "vagabond_swords": 2})
    assert r.status_code == 202
    assert signals(server) == [DiceRolled((3, 0)), DiceRolled((1, 2), 2)]


@pytest.mark.parametrize(
    "body",
    [
        {"rolls": [4, 0]},
        {"rolls": [-1, 0]},
        {"rolls": [1]},
        {"rolls": [1, 2, 3]},
        {"rolls": [1, 2], "vagabond_swords": -1},
    ],
)
def test_bad_dice(client, server, body):
    assert client.post("/signals/dice", json=body).status_code == 422
    assert signals(server) == []


def test_state(client, server):
    assert client.get("/state").json() == {
        "step": "starting",
        "waiting_for": None,
        "scores": {},
        "message": "",
        "error": None,
    }
    server.set_status(
        step="idle",
        waiting_for="your_turn",
        scores={Faction.MARQUISE_BOT: 7, Faction.EYRIE: 3},
        message="remove 2 warriors",
    )
    assert client.get("/state").json() == {
        "step": "idle",
        "waiting_for": "your_turn",
        "scores": {"marquise_bot": 7, "eyrie": 3},
        "message": "remove 2 warriors",
        "error": None,
    }


def test_cors(server):
    client = TestClient(create_app(server, ["*"]))
    r = client.get("/state", headers={"Origin": "http://phone.local"})
    assert r.headers["access-control-allow-origin"] == "*"


def _cfg(port=0):
    return {
        "operator_server": {
            "host": "127.0.0.1",
            "port": port,
            "cors_origins": [],
            "start_timeout_s": 5,
        }
    }


def test_background_thread(server):
    api = start_operator_api(server, _cfg())
    try:
        base = f"http://127.0.0.1:{api.port}"
        request = urllib.request.Request(f"{base}/signals/your-turn", method="POST")
        with urllib.request.urlopen(request, timeout=5) as r:
            assert r.status == 202
        assert signals(server) == [YourTurn()]
        with urllib.request.urlopen(f"{base}/state", timeout=5) as r:
            assert json.load(r)["step"] == "starting"
    finally:
        api.stop()
    assert not api._thread.is_alive()


def test_port_in_use(server):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        with pytest.raises(TemporaryError, match="port in use"):
            start_operator_api(server, _cfg(busy.getsockname()[1]))
