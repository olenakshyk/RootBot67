import pytest

from rootbot.app.operator_ui import (
    CardGiven,
    DiceRolled,
    EndGame,
    OperatorServer,
    OrderCardRevealed,
    SignalRejected,
    WaitingFor,
    YourTurn,
)
from rootbot.common.types import Faction, OrderCard, Suit

CARD = OrderCard(Suit.FOX, ())
DIE_MAX = 3
WAITING_STATES = [None, *WaitingFor]


def server(waiting_for):
    """Сервер, що чекає `waiting_for`."""
    s = OperatorServer(die_max=DIE_MAX)
    s.set_status(waiting_for=waiting_for)
    return s


SENDERS = {
    "YourTurn": lambda s: s.your_turn(),
    "CardGiven": lambda s: s.card_given(),
    "OrderCardRevealed": lambda s: s.order_card(CARD),
    "DiceRolled": lambda s: s.dice_rolled((1, 2)),
}
ACCEPTED = {
    WaitingFor.YOUR_TURN: {"YourTurn", "CardGiven"},
    WaitingFor.ORDER_CARD: {"OrderCardRevealed"},
    WaitingFor.DICE: {"DiceRolled"},
}
REJECTED_CASES = [
    (waiting_for, name)
    for waiting_for in WAITING_STATES
    for name in SENDERS
    if name not in ACCEPTED.get(waiting_for, set())
]


# --- прийняті сигнали ---


def test_your_turn_accepted_while_waiting_for_your_turn():
    s = server(WaitingFor.YOUR_TURN)
    s.your_turn()
    assert s.next_signal() == YourTurn()


def test_card_given_accepted_while_waiting_for_your_turn():
    s = server(WaitingFor.YOUR_TURN)
    s.card_given()
    assert s.next_signal() == CardGiven()


def test_order_card_accepted_while_waiting_for_order_card():
    s = server(WaitingFor.ORDER_CARD)
    s.order_card(CARD)
    assert s.next_signal() == OrderCardRevealed(CARD)


def test_dice_accepted_while_waiting_for_dice():
    s = server(WaitingFor.DICE)
    s.dice_rolled((3, 0), vagabond_swords=2)
    assert s.next_signal() == DiceRolled((3, 0), 2)


@pytest.mark.parametrize("waiting_for", WAITING_STATES)
def test_end_game_accepted_in_any_state(waiting_for):
    s = server(waiting_for)
    s.end_game()
    assert s.next_signal() == EndGame()
    assert s.current_state().waiting_for == waiting_for


# --- відхилені сигнали ---


@pytest.mark.parametrize(("waiting_for", "name"), REJECTED_CASES)
def test_unexpected_signal_rejected(waiting_for, name):
    s = server(waiting_for)
    with pytest.raises(SignalRejected, match=name):
        SENDERS[name](s)
    assert s.next_signal() is None
    assert s.current_state().waiting_for == waiting_for


def test_card_given_rejected_while_waiting_for_dice():
    s = server(WaitingFor.DICE)
    with pytest.raises(SignalRejected, match="CardGiven.*DICE"):
        s.card_given()
    assert s.next_signal() is None
    assert s.current_state().waiting_for == WaitingFor.DICE


# --- захист від повторів ---


def test_second_dice_rejected():
    s = server(WaitingFor.DICE)
    s.dice_rolled((1, 2))
    with pytest.raises(SignalRejected):
        s.dice_rolled((1, 2))
    assert s.next_signal() == DiceRolled((1, 2))
    assert s.next_signal() is None


@pytest.mark.parametrize(
    ("waiting_for", "send"),
    [
        (WaitingFor.YOUR_TURN, lambda s: s.your_turn()),
        (WaitingFor.ORDER_CARD, lambda s: s.order_card(CARD)),
        (WaitingFor.DICE, lambda s: s.dice_rolled((0, 0))),
    ],
)
def test_answer_clears_waiting_for(waiting_for, send):
    s = server(waiting_for)
    send(s)
    assert s.current_state().waiting_for is None


def test_card_given_keeps_waiting_for_your_turn():
    s = server(WaitingFor.YOUR_TURN)
    s.card_given()
    s.card_given()
    assert s.current_state().waiting_for == WaitingFor.YOUR_TURN
    assert [s.next_signal(), s.next_signal()] == [CardGiven(), CardGiven()]


# --- перевірка карти наказу ---


def test_normal_order_card_accepted():
    card = OrderCard(Suit.BIRD, (Suit.MOUSE, Suit.ANY))
    s = server(WaitingFor.ORDER_CARD)
    s.order_card(card)
    assert s.next_signal() == OrderCardRevealed(card)


@pytest.mark.parametrize(
    "card",
    [OrderCard(Suit.ANY, ()), OrderCard(Suit.FOX, (Suit.BIRD,))],
)
def test_invalid_order_card_rejected(card):
    s = server(WaitingFor.ORDER_CARD)
    with pytest.raises(SignalRejected, match="OrderCardRevealed"):
        s.order_card(card)
    assert s.next_signal() is None
    assert s.current_state().waiting_for == WaitingFor.ORDER_CARD


# --- перевірка кидка ---


def test_dice_list_accepted_as_tuple():
    s = server(WaitingFor.DICE)
    s.dice_rolled([3, 0])
    assert s.next_signal() == DiceRolled((3, 0))


def test_many_vagabond_swords_accepted():
    s = server(WaitingFor.DICE)
    s.dice_rolled((1, 1), vagabond_swords=5)
    assert s.next_signal() == DiceRolled((1, 1), 5)


@pytest.mark.parametrize(
    ("rolls", "vagabond_swords"),
    [
        ((1, 2, 3), None),
        ((1,), None),
        ((4, 0), None),
        ((-1, 0), None),
        ((1, 2), -1),
    ],
)
def test_invalid_dice_rejected(rolls, vagabond_swords):
    s = server(WaitingFor.DICE)
    with pytest.raises(SignalRejected, match="DiceRolled"):
        s.dice_rolled(rolls, vagabond_swords)
    assert s.next_signal() is None
    assert s.current_state().waiting_for == WaitingFor.DICE


# --- стан ---


def test_scores_unchanged_when_caller_changes_its_dict():
    s = OperatorServer(die_max=DIE_MAX)
    scores = {Faction.EYRIE: 3}
    s.set_status(scores=scores)
    scores[Faction.EYRIE] = 10
    assert s.current_state().scores == {Faction.EYRIE: 3}


def test_set_status_replaces_only_passed_fields():
    s = server(WaitingFor.DICE)
    s.set_status(step="battle", error=None)
    assert s.current_state().waiting_for == WaitingFor.DICE
    assert s.current_state().step == "battle"


def test_set_status_rejects_plain_string_waiting_for():
    s = server(None)
    with pytest.raises(TypeError, match="dices"):
        s.set_status(waiting_for="dices")
    assert s.current_state().waiting_for is None
