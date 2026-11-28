from pathlib import Path

import pytest

from rootbot.common.types import ClearingId, Faction, PieceCounts
from rootbot.game.board import load_board
from rootbot.game.state import BOT, GameState, GameStateError, copy_pieces

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"
EYRIE, ALLIANCE, VAGABOND = Faction.EYRIE, Faction.ALLIANCE, Faction.VAGABOND
C = ClearingId(5)  # будь-яка галявина, для тестів не важливо яка


@pytest.fixture(scope="module")
def board():
    return load_board(AUTUMN)


def pc(w=0, b=0, t=0, p=0):
    return PieceCounts(warriors=w, buildings=b, tokens=t, pawn=p)


def state(board, here=None, scores=None):
    """Стан з фігурками `here` на галявині C."""
    return GameState(board, pieces={C: here or {}}, scores=scores or {})


# --- панування ---


def test_empty_clearing_nobody_rules(board):
    assert state(board).ruling_faction(C) is None


def test_clearing_without_entry_nobody_rules(board):
    assert GameState(board).ruling_faction(ClearingId(1)) is None


def test_majority_rules(board):
    s = state(board, {BOT: pc(w=3), ALLIANCE: pc(w=2)})
    assert s.ruling_faction(C) == BOT


def test_buildings_count_for_rule(board):
    assert state(board, {BOT: pc(w=2), ALLIANCE: pc(w=1, b=2)}).ruling_faction(C) == ALLIANCE


def test_tie_nobody_rules(board):
    s = state(board, {BOT: pc(w=2), ALLIANCE: pc(w=1, b=1)})
    assert s.ruling_faction(C) is None


def test_tokens_do_not_count_for_rule(board):
    assert state(board, {BOT: pc(w=1), ALLIANCE: pc(t=3)}).ruling_faction(C) == BOT


def test_vagabond_never_rules(board):
    s = state(board, {VAGABOND: pc(p=1)})
    assert s.ruling_faction(C) is None


def test_rule_compares_each_faction_separately(board):
    # 2 проти 1 і 1: бот панує, хоча ворогів разом стільки ж
    assert state(board, {BOT: pc(w=2), EYRIE: pc(w=1), ALLIANCE: pc(b=1)}).ruling_faction(C) == BOT


def test_eyrie_rule_ties(board):
    assert state(board, {BOT: pc(w=2), EYRIE: pc(w=1, b=1)}).ruling_faction(C) == EYRIE


def test_eyrie_rule_three_way_tie(board):
    s = state(board, {BOT: pc(w=1), EYRIE: pc(b=1), ALLIANCE: pc(w=1)})
    assert s.ruling_faction(C) == EYRIE


def test_eyrie_loses_without_tie(board):
    assert state(board, {BOT: pc(w=3), EYRIE: pc(w=2)}).ruling_faction(C) == BOT


def test_eyrie_with_only_tokens_does_not_rule(board):
    assert state(board, {EYRIE: pc(t=1)}).ruling_faction(C) is None


# --- ворожі фігурки ---


def test_enemy_pieces_counts_everything_but_bot(board):
    s = state(board, {BOT: pc(w=4), EYRIE: pc(w=1, b=1), ALLIANCE: pc(t=2), VAGABOND: pc(p=1)})
    assert s.enemy_pieces(C) == 5


def test_enemy_pieces_empty(board):
    assert state(board, {BOT: pc(w=3)}).enemy_pieces(C) == 0


def test_enemies_skip_zero_counts(board):
    s = state(board, {BOT: pc(w=1), EYRIE: pc(), VAGABOND: pc(p=1)})
    assert s.enemies(C) == {VAGABOND: pc(p=1)}


def test_enemies_in_setup_order(board):
    s = state(board, {VAGABOND: pc(p=1), BOT: pc(w=1), ALLIANCE: pc(t=1), EYRIE: pc(w=2)})
    assert list(s.enemies(C)) == [EYRIE, ALLIANCE, VAGABOND]


def test_counts_default_to_zero(board):
    assert state(board).counts(C, EYRIE) == pc()


# --- вибір захисника ---


def test_no_enemies_no_defender(board):
    assert state(board, {BOT: pc(w=3)}).defender(C) is None


def test_single_enemy(board):
    assert state(board, {BOT: pc(w=1), VAGABOND: pc(p=1)}).defender(C) == VAGABOND


def test_defender_most_pieces(board):
    # жетони теж фігурки: у Альянсу 3, у Династії 2
    s = state(board, {BOT: pc(w=1), EYRIE: pc(w=2), ALLIANCE: pc(w=1, t=2)})
    assert s.defender(C) == ALLIANCE


def test_defender_tie_most_points(board):
    s = state(board, {EYRIE: pc(w=2), ALLIANCE: pc(w=2)}, scores={EYRIE: 7, ALLIANCE: 12})
    assert s.defender(C) == ALLIANCE


def test_defender_tie_missing_score_is_zero(board):
    s = state(board, {EYRIE: pc(w=2), ALLIANCE: pc(w=2)}, scores={ALLIANCE: 1})
    assert s.defender(C) == ALLIANCE


def test_defender_full_tie_setup_order(board):
    here = {EYRIE: pc(w=2), ALLIANCE: pc(w=2)}
    scores = {EYRIE: 5, ALLIANCE: 5}
    assert state(board, here, scores).defender(C) == EYRIE
    assert state(board, {ALLIANCE: pc(w=2), VAGABOND: pc(p=2)}, scores).defender(C) == ALLIANCE


def test_defender_ignores_bot_score(board):
    s = state(board, {BOT: pc(w=5), EYRIE: pc(w=1)}, scores={BOT: 29})
    assert s.defender(C) == EYRIE


# --- галявини з фігурками фракції ---


def test_clearings_with_in_priority_order(board):
    first, second, third = (c.id for c in board.by_priority()[:3])
    s = GameState(board, pieces={third: {EYRIE: pc(w=1)}, first: {EYRIE: pc(t=1)}})
    s = GameState(board, pieces={**s.pieces, second: {EYRIE: pc()}})  # нулі не рахуються
    assert s.clearings_with(EYRIE) == [first, third]
    assert s.clearings_with(ALLIANCE) == []


def test_clearings_with_warriors_only(board):
    s = GameState(board, pieces={ClearingId(1): {BOT: pc(b=1)}, ClearingId(2): {BOT: pc(w=1)}})
    assert set(s.clearings_with(BOT)) == {ClearingId(1), ClearingId(2)}
    assert s.clearings_with(BOT, warriors_only=True) == [ClearingId(2)]


# --- некоректний стан ---


def test_unknown_clearing_query(board):
    with pytest.raises(GameStateError, match="unknown clearing 13"):
        state(board).ruling_faction(ClearingId(13))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pieces": {ClearingId(13): {}}}, "pieces: unknown clearing 13"),
        (
            {"pieces": {C: {Faction.LIZARDS: PieceCounts(warriors=1)}}},
            "unsupported faction lizards",
        ),
        ({"pieces": {C: {EYRIE: PieceCounts(warriors=-1)}}}, r"negative eyrie \['warriors'\]"),
        ({"keep_clearing": ClearingId(0)}, "keep_clearing: unknown clearing 0"),
        ({"scores": {Faction.RIVERFOLK: 3}}, "scores: unsupported faction riverfolk"),
        ({"scores": {EYRIE: -1}}, "negative score for eyrie"),
    ],
)
def test_invalid_state(board, kwargs, message):
    with pytest.raises(GameStateError, match=message):
        GameState(board, **kwargs)


def test_reports_all_errors(board):
    with pytest.raises(GameStateError) as exc:
        GameState(board, scores={EYRIE: -1}, keep_clearing=ClearingId(0))
    assert "negative score" in str(exc.value)
    assert "keep_clearing" in str(exc.value)


# --- update з огляду ---


def test_update_takes_pieces_and_others_scores_from_camera(board):
    s = GameState(board, pieces={C: {BOT: pc(w=2)}}, scores={BOT: 7, EYRIE: 3})
    s.update(pieces={C: {EYRIE: pc(w=1)}}, keep_clearing=None, scores={BOT: 0, EYRIE: 5})
    assert s.counts(C, BOT).warriors == 0 and s.counts(C, EYRIE).warriors == 1
    assert s.scores == {EYRIE: 5, BOT: 7}  # свій рахунок бот веде сам


def test_update_keeps_last_known_scores_not_read(board):
    s = GameState(board, scores={BOT: 7, EYRIE: 3, ALLIANCE: 4})
    s.update(pieces={}, keep_clearing=None, scores={EYRIE: 5})
    assert s.scores == {BOT: 7, EYRIE: 5, ALLIANCE: 4}


def test_update_keeps_keep_if_not_seen(board):
    s = GameState(board, keep_clearing=C)
    s.update(pieces={}, keep_clearing=None, scores={})
    assert s.keep_clearing == C


def test_update_keeps_keep_if_its_clearing_outside_partial_update(board):
    s = GameState(board, keep_clearing=C)
    s.update(pieces={C: {}}, keep_clearing=None, scores={}, clearings=[ClearingId(1)])
    assert s.keep_clearing == C


@pytest.mark.parametrize("seen_keep", [None, ClearingId(1)])
def test_update_takes_keep_from_camera_if_its_clearing_seen(board, seen_keep):
    s = GameState(board, keep_clearing=C)
    s.update(pieces={C: {}, ClearingId(1): {}}, keep_clearing=seen_keep, scores={})
    assert s.keep_clearing == seen_keep


@pytest.mark.parametrize("pieces", [{C: {}, ClearingId(0): {}}, {C: {EYRIE: pc(w=-1)}}])
def test_invalid_update_leaves_state_unchanged(board, pieces):
    s = GameState(board, pieces={C: {BOT: pc(w=2)}}, keep_clearing=C, scores={BOT: 7, EYRIE: 3})
    with pytest.raises(GameStateError, match="invalid game state"):
        s.update(pieces=pieces, keep_clearing=None, scores={EYRIE: 5})
    assert (s.pieces, s.keep_clearing, s.scores) == ({C: {BOT: pc(w=2)}}, C, {BOT: 7, EYRIE: 3})


def test_add_warriors_more_than_present_raises(board):
    s = state(board, {BOT: pc(w=2)})
    with pytest.raises(GameStateError, match="has 2 marquise_bot warriors, cannot change by -3"):
        s.add_warriors(C, -3)
    assert s.pieces == {C: {BOT: pc(w=2)}}


def test_add_warriors_unknown_clearing_raises(board):
    s = state(board, {BOT: pc(w=2)})
    with pytest.raises(GameStateError, match="unknown clearing 0"):
        s.add_warriors(ClearingId(0), 1)
    assert s.pieces == {C: {BOT: pc(w=2)}}


def test_partial_update_touches_only_given_clearings(board):
    other = ClearingId(1)
    s = GameState(board, pieces={C: {BOT: pc(w=2)}, other: {BOT: pc(w=4)}})
    s.update(pieces={C: {}}, keep_clearing=None, scores={}, clearings=[C])
    assert s.counts(C, BOT).warriors == 0 and s.counts(other, BOT).warriors == 4


def test_copy_pieces_changes_leave_original_untouched():
    original = {C: {BOT: pc(w=2)}}
    copied = copy_pieces(original)
    copied[C][BOT].warriors = 5
    copied[C][EYRIE] = pc(w=1)
    copied[ClearingId(1)] = {}
    assert original == {C: {BOT: pc(w=2)}}
