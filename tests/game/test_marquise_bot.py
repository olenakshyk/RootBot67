from pathlib import Path

import pytest

from rootbot.common.types import ClearingId, Faction, OrderCard, PieceCounts, Suit
from rootbot.game.actions import Battle, BattleResult, Move, Recruit, Score
from rootbot.game.board import load_board
from rootbot.game.marquise_bot import (
    battles,
    birdsong,
    card_given,
    evening,
    moves,
    order_clearings,
    recruits,
    resolve_battle,
    restarts_day,
)
from rootbot.game.state import BOT, GameState

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"
ALLIANCE, EYRIE, VAGABOND = Faction.ALLIANCE, Faction.EYRIE, Faction.VAGABOND
RULES = {"points_per_clearing": 2, "min_warriors": 3}  # як у config/default.yaml


@pytest.fixture(scope="module")
def board():
    return load_board(AUTUMN)


def pc(w=0, b=0, t=0, p=0):
    return PieceCounts(warriors=w, buildings=b, tokens=t, pawn=p)


def state(board, pieces):
    return GameState(board, pieces={ClearingId(c): here for c, here in pieces.items()})


# --- Світанок ---


def score(board, pieces, **rules):
    return birdsong(state(board, pieces), **(rules or RULES))


def test_birdsong_empty_board_scores_zero(board):
    assert birdsong(GameState(board), **RULES) == Score(0, "birdsong: scoring clearings none")


def test_birdsong_scores_ruled_clearing_with_three_warriors(board):
    assert score(board, {1: {BOT: pc(w=3)}}).points == 2


def test_birdsong_two_warriors_not_enough(board):
    assert score(board, {1: {BOT: pc(w=2)}}).points == 0


def test_birdsong_needs_rule_not_just_warriors(board):
    assert score(board, {1: {BOT: pc(w=3), ALLIANCE: pc(w=4)}}).points == 0


def test_birdsong_tie_is_not_rule(board):
    assert score(board, {1: {BOT: pc(w=3), ALLIANCE: pc(w=2, b=1)}}).points == 0


def test_birdsong_eyrie_wins_tie(board):
    assert score(board, {1: {BOT: pc(w=3), EYRIE: pc(w=3)}}).points == 0


def test_birdsong_enemy_tokens_do_not_break_rule(board):
    assert score(board, {1: {BOT: pc(w=3), ALLIANCE: pc(t=5)}}).points == 2


def test_birdsong_sums_clearings_in_priority_order(board):
    first, second = (c.id for c in board.by_priority()[:2])
    result = score(board, {second: {BOT: pc(w=5)}, first: {BOT: pc(w=3)}})
    assert result == Score(4, f"birdsong: scoring clearings {first}, {second}")


def test_birdsong_uses_given_rules(board):
    assert score(board, {1: {BOT: pc(w=1)}}, points_per_clearing=3, min_warriors=1).points == 3


# --- День: галявини масті карти ---


def card(suit):
    return OrderCard(suit=suit, craft_cost=())


@pytest.mark.parametrize("suit", [Suit.FOX, Suit.RABBIT, Suit.MOUSE])
def test_order_clearings_of_card_suit_in_priority_order(board, suit):
    expected = [c for c in board.by_priority() if c.suit == suit]
    assert order_clearings(board, card(suit)) == expected
    assert len(expected) == 4


def test_order_clearings_bird_means_all(board):
    assert order_clearings(board, card(Suit.BIRD)) == board.by_priority()


# --- День: бій ---


def fox_clearings(board):
    return [c.id for c in board.by_priority(Suit.FOX)]


def test_battle_where_bot_and_enemy_meet(board):
    c = fox_clearings(board)[0]
    s = state(board, {c: {BOT: pc(w=1), ALLIANCE: pc(t=1)}})
    assert battles(s, card(Suit.FOX)) == [Battle(c, ALLIANCE)]


def test_no_battle_on_other_suit(board):
    other = board.by_priority(Suit.MOUSE)[0].id
    s = state(board, {other: {BOT: pc(w=3), ALLIANCE: pc(w=1)}})
    assert battles(s, card(Suit.FOX)) == []


def test_no_battle_without_bot_warriors(board):
    c = fox_clearings(board)[0]
    assert battles(state(board, {c: {ALLIANCE: pc(w=2)}}), card(Suit.FOX)) == []


def test_no_battle_without_enemies(board):
    c = fox_clearings(board)[0]
    assert battles(state(board, {c: {BOT: pc(w=5)}}), card(Suit.FOX)) == []


def test_battle_against_lone_vagabond_pawn(board):
    c = fox_clearings(board)[0]
    s = state(board, {c: {BOT: pc(w=1), VAGABOND: pc(p=1)}})
    assert battles(s, card(Suit.FOX)) == [Battle(c, VAGABOND)]


def test_battle_picks_defender_with_most_pieces(board):
    c = fox_clearings(board)[0]
    s = state(board, {c: {BOT: pc(w=2), EYRIE: pc(w=1), ALLIANCE: pc(w=1, t=1)}})
    assert battles(s, card(Suit.FOX)) == [Battle(c, ALLIANCE)]


def test_battles_on_every_matching_clearing_in_priority_order(board):
    first, *_, last = fox_clearings(board)
    s = state(
        board,
        {
            last: {BOT: pc(w=1), EYRIE: pc(w=1)},
            first: {BOT: pc(w=1), ALLIANCE: pc(w=1)},
        },
    )
    assert battles(s, card(Suit.FOX)) == [Battle(first, ALLIANCE), Battle(last, EYRIE)]


def test_bird_card_battles_on_all_suits(board):
    mouse, rabbit = board.by_priority(Suit.MOUSE)[0].id, board.by_priority(Suit.RABBIT)[0].id
    s = state(
        board,
        {
            mouse: {BOT: pc(w=1), EYRIE: pc(w=1)},
            rabbit: {BOT: pc(w=1), ALLIANCE: pc(w=1)},
        },
    )
    assert {b.clearing for b in battles(s, card(Suit.BIRD))} == {mouse, rabbit}


# --- День: результат бою ---


def fight(board, bot, enemy, rolls, defender=EYRIE, keep=False, **kwargs):
    """Бій бота з `defender` на галявині 1; `bot` і `enemy` це їхні фігурки там."""
    c = ClearingId(1)
    s = GameState(
        board,
        pieces={c: {BOT: bot, defender: enemy}},
        keep_clearing=c if keep else ClearingId(2),
    )
    return resolve_battle(s, Battle(c, defender), rolls, **kwargs)


def test_attacker_gets_higher_roll(board):
    result = fight(board, pc(w=5), pc(w=5), (1, 3))
    assert result == BattleResult(bot_warriors_lost=1, keep_lost=False, defender_hits=3)


def test_alliance_defender_gets_higher_roll(board):
    result = fight(board, pc(w=5), pc(w=5), (1, 3), defender=ALLIANCE)
    assert result == BattleResult(bot_warriors_lost=3, keep_lost=False, defender_hits=1)


def test_hits_capped_by_warriors(board):
    result = fight(board, pc(w=1), pc(w=2, b=2), (3, 3))
    assert result == BattleResult(bot_warriors_lost=1, keep_lost=False, defender_hits=1)


def test_buildings_do_not_deal_hits(board):
    result = fight(board, pc(w=3), pc(w=1, b=2), (3, 3))
    assert result.bot_warriors_lost == 1


def test_defenseless_defender_takes_extra_hit(board):
    result = fight(board, pc(w=3), pc(b=2, t=1), (2, 0))
    assert result == BattleResult(bot_warriors_lost=0, keep_lost=False, defender_hits=3)


def test_defender_hits_capped_by_pieces(board):
    assert fight(board, pc(w=3), pc(b=1, t=1), (2, 0)).defender_hits == 2
    assert fight(board, pc(w=5), pc(w=1), (3, 3)).defender_hits == 1


def test_extra_hit_even_on_zero_roll(board):
    assert fight(board, pc(w=1), pc(t=1), (0, 0)).defender_hits == 1


def test_bot_loses_keep_when_out_of_warriors(board):
    result = fight(board, pc(w=1), pc(w=3), (3, 2), keep=True)
    assert result == BattleResult(bot_warriors_lost=1, keep_lost=True, defender_hits=1)


def test_keep_safe_while_warriors_remain(board):
    assert not fight(board, pc(w=3), pc(w=3), (3, 2), keep=True).keep_lost


def test_keep_elsewhere_not_lost(board):
    assert not fight(board, pc(w=1), pc(w=3), (3, 2)).keep_lost


def test_vagabond_hits_capped_by_swords(board):
    result = fight(board, pc(w=5), pc(p=1), (3, 3), defender=VAGABOND, vagabond_swords=1)
    assert result == BattleResult(bot_warriors_lost=1, keep_lost=False, defender_hits=3)


def test_vagabond_without_swords_is_defenseless(board):
    result = fight(board, pc(w=5), pc(p=1), (2, 1), defender=VAGABOND, vagabond_swords=0)
    assert result == BattleResult(bot_warriors_lost=0, keep_lost=False, defender_hits=3)


def test_vagabond_battle_requires_swords(board):
    with pytest.raises(ValueError, match="vagabond_swords"):
        fight(board, pc(w=5), pc(p=1), (2, 1), defender=VAGABOND)


@pytest.mark.parametrize("rolls", [(4, 0), (-1, 2), (1,)])
def test_invalid_rolls_rejected(board, rolls):
    with pytest.raises(ValueError, match="rolls"):
        fight(board, pc(w=1), pc(w=1), rolls)


# --- День: рух ---
# Мапа «Осінь»: галявина 1 (лис, пріоритет 1) межує з 2 (пріоритет 5), 4 (10) і 5 (9);
# галявина 2 (кролик, пріоритет 5) межує з 1 і 3 (миша, пріоритет 2).

STAY = {"stay_warriors": 3}  # як у config/default.yaml


def move(board, pieces, suit=Suit.FOX, **rules):
    return moves(state(board, pieces), card(suit), **(rules or STAY))


def test_no_move_with_three_warriors(board):
    assert move(board, {1: {BOT: pc(w=3)}}) == []


def test_moves_all_but_three(board):
    assert move(board, {1: {BOT: pc(w=7)}, 4: {EYRIE: pc(w=1)}}) == [Move(1, 4, 4)]


def test_moves_to_neighbour_with_most_enemy_pieces(board):
    pieces = {1: {BOT: pc(w=4)}, 4: {ALLIANCE: pc(t=2)}, 5: {EYRIE: pc(w=1)}}
    assert move(board, pieces) == [Move(1, 4, 1)]


def test_enemy_tie_goes_to_lower_priority_number(board):
    pieces = {1: {BOT: pc(w=4)}, 4: {ALLIANCE: pc(t=1)}, 5: {EYRIE: pc(w=1)}}
    assert move(board, pieces) == [Move(1, 5, 1)]  # 5 має пріоритет 9, а 4 має 10


def test_no_enemies_goes_to_lowest_priority_number(board):
    assert move(board, {1: {BOT: pc(w=4)}}) == [Move(1, 2, 1)]  # 2 має пріоритет 5


def test_no_move_without_rule(board):
    assert move(board, {1: {BOT: pc(w=4), EYRIE: pc(w=4)}}) == []


def test_bot_rules_despite_enemies(board):
    assert move(board, {1: {BOT: pc(w=5), ALLIANCE: pc(w=2)}}) == [Move(1, 2, 2)]


def test_no_move_from_other_suit(board):
    assert move(board, {1: {BOT: pc(w=5)}}, suit=Suit.RABBIT) == []


def test_arrived_warriors_move_on_from_later_clearing(board):
    pieces = {1: {BOT: pc(w=5)}, 2: {BOT: pc(w=2)}, 3: {ALLIANCE: pc(t=1)}}
    # 1 → 2 (два воїни), далі в 2 їх стало 4, і 2 (пріоритет 5) іде в 3 з ворогом.
    assert move(board, pieces, suit=Suit.BIRD) == [Move(1, 2, 2), Move(2, 3, 1)]


def test_arrived_warriors_stay_in_already_visited_clearing(board):
    pieces = {2: {BOT: pc(w=4)}, 3: {BOT: pc(w=3), ALLIANCE: pc(t=1)}}
    # 2 (пріоритет 5) шле воїна в 3, де їх стає 4, але 3 (пріоритет 2) уже пройдено.
    assert move(board, pieces, suit=Suit.BIRD) == [Move(2, 3, 1)]


def test_moves_do_not_change_given_state(board):
    s = state(board, {1: {BOT: pc(w=5)}})
    moves(s, card(Suit.FOX), **STAY)
    assert s.counts(ClearingId(1), BOT).warriors == 5
    assert s.counts(ClearingId(2), BOT).warriors == 0


def test_moves_use_given_rules(board):
    assert move(board, {1: {BOT: pc(w=2)}}, stay_warriors=1) == [Move(1, 2, 1)]


# --- День: набір ---
# Мапа «Осінь»: галявини лиса в порядку пріоритету 1, 8, 10, 6; галявина 3 миша (пріоритет 2).

FOX, RABBIT, MOUSE, BIRD, ANY = Suit.FOX, Suit.RABBIT, Suit.MOUSE, Suit.BIRD, Suit.ANY
SUPPLY = {"total_warriors": 25}  # як у config/default.yaml


def recruit(board, pieces, cost, keep=None, **rules):
    s = GameState(
        board,
        pieces={ClearingId(c): here for c, here in pieces.items()},
        keep_clearing=keep,
    )
    return recruits(s, OrderCard(suit=FOX, craft_cost=cost), **(rules or SUPPLY))


BOT_AT_1_8_3 = {1: {BOT: pc(w=1)}, 8: {BOT: pc(w=1)}, 3: {BOT: pc(w=1)}}


@pytest.mark.parametrize("n", [1, 2, 3])
def test_recruit_one_suit_on_ruled_clearings_of_that_suit(board, n):
    assert recruit(board, BOT_AT_1_8_3, (FOX,) * n) == [Recruit(1, n), Recruit(8, n)]


def test_recruit_skips_clearings_bot_does_not_rule(board):
    pieces = {1: {BOT: pc(w=1), EYRIE: pc(w=1)}, 8: {BOT: pc(w=1), ALLIANCE: pc(w=1)}}
    assert recruit(board, pieces, (FOX,)) == []


def test_recruit_one_of_each_suit_on_all_ruled_clearings(board):
    expected = [Recruit(1, 1), Recruit(3, 1), Recruit(8, 1)]
    assert recruit(board, BOT_AT_1_8_3, (MOUSE, FOX, RABBIT)) == expected


def test_recruit_four_any_into_keep(board):
    pieces = {1: {BOT: pc(w=1), EYRIE: pc(w=2)}}  # Keep не потребує панування
    assert recruit(board, pieces, (ANY,) * 4, keep=ClearingId(1)) == [Recruit(1, 4)]


def test_recruit_four_any_without_keep(board):
    assert recruit(board, BOT_AT_1_8_3, (ANY,) * 4) == []


def test_no_craft_cost_restarts_day(board):
    assert recruit(board, BOT_AT_1_8_3, ()) == []
    assert restarts_day(OrderCard(suit=FOX, craft_cost=()))
    assert not restarts_day(OrderCard(suit=FOX, craft_cost=(FOX,)))


def test_recruit_limited_by_supply_in_priority_order(board):
    # На мапі 3 воїни, усього 7, отже в запасі 4: галявина 1 отримує 3, галявина 8 лише 1.
    assert recruit(board, BOT_AT_1_8_3, (FOX,) * 3, total_warriors=7) == [
        Recruit(1, 3),
        Recruit(8, 1),
    ]


def test_recruit_empty_supply(board):
    assert recruit(board, BOT_AT_1_8_3, (FOX,), total_warriors=3) == []


@pytest.mark.parametrize(
    "cost",
    [(FOX, MOUSE), (ANY,), (BIRD,), (BIRD,) * 4, (FOX, FOX, MOUSE, RABBIT), (FOX,) * 5],
)
def test_recruit_rejects_unknown_craft_cost(board, cost):
    with pytest.raises(ValueError, match="craft cost"):
        recruit(board, BOT_AT_1_8_3, cost)


# --- Вечір і риси бота ---


@pytest.mark.parametrize(("left", "draw"), [(0, 5), (3, 2), (5, 0)])
def test_evening_draws_up_to_schedule_size(left, draw):
    assert evening(left, schedule_size=5) == draw


@pytest.mark.parametrize("left", [-1, 6])
def test_evening_rejects_impossible_schedule(left):
    with pytest.raises(ValueError, match="cards_in_schedule"):
        evening(left, schedule_size=5)


def test_card_given_scores_for_bot():
    assert card_given(points=1) == Score(1, "poor manual dexterity: card given to bot")
