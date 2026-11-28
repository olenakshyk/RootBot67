import copy
from pathlib import Path

import pytest
import yaml

from rootbot.common.errors import FatalError
from rootbot.common.types import BoardPoint, Suit
from rootbot.game.board import BoardError, create_board, load_board, parse_board

AUTUMN = Path(__file__).resolve().parents[2] / "config" / "maps" / "autumn.yaml"


@pytest.fixture(scope="module")
def autumn_data():
    with open(AUTUMN, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def data(autumn_data):
    return copy.deepcopy(autumn_data)


def clearing(data, cid):
    return next(c for c in data["clearings"] if c["id"] == cid)


# --- справжня мапа ---


def test_autumn_loads():
    board = load_board(AUTUMN)
    assert sorted(board.clearings) == list(range(1, 13))


def test_autumn_corners():
    board = load_board(AUTUMN)
    assert sorted(c.id for c in board.corners()) == [1, 3, 9, 12]


def test_autumn_four_of_each_suit():
    board = load_board(AUTUMN)
    for suit in (Suit.FOX, Suit.RABBIT, Suit.MOUSE):
        clearings = board.by_priority(suit)
        assert len(clearings) == 4
        assert all(c.suit == suit for c in clearings)
    assert board.by_priority(Suit.BIRD) == []


def test_autumn_priority_order():
    board = load_board(AUTUMN)
    assert [c.priority for c in board.by_priority()] == list(range(1, 13))
    fox = [c.priority for c in board.by_priority(Suit.FOX)]
    assert fox == sorted(fox)


def test_autumn_adjacency_symmetric():
    board = load_board(AUTUMN)
    for c in board.clearings.values():
        for other in board.adjacent(c.id):
            assert c.id in board.adjacent(other)


def test_autumn_centers_inside_board():
    board = load_board(AUTUMN)
    for c in board.clearings.values():
        assert 0 < c.center.x < 570 and 0 < c.center.y < 440


def test_clearing_fields(data):
    c = parse_board(data)[1]
    assert c.suit == Suit.FOX
    assert c.corner is True
    assert c.adjacent == (2, 4, 5)
    assert isinstance(c.center, BoardPoint)


# --- зламані мапи ---


def assert_invalid(data, message):
    with pytest.raises(BoardError, match=message):
        parse_board(data)


def test_missing_clearings_list():
    assert_invalid({}, "'clearings' list")
    assert_invalid(None, "'clearings' list")


def test_wrong_count(data):
    data["clearings"].pop()
    assert_invalid(data, "expected 12 clearings")


def test_duplicate_id(data):
    clearing(data, 2)["id"] = 1
    assert_invalid(data, "duplicate id")


def test_id_out_of_range(data):
    clearing(data, 12)["id"] = 13
    assert_invalid(data, r"ids must be 1\.\.12")


def test_duplicate_priority(data):
    clearing(data, 2)["priority"] = clearing(data, 1)["priority"]
    assert_invalid(data, "priorities must be unique")


def test_missing_priority(data):
    clearing(data, 2)["priority"] = None
    assert_invalid(data, "clearing 2: 'priority' must be an integer")


def test_bad_suit(data):
    clearing(data, 2)["suit"] = "bird"
    assert_invalid(data, "clearing 2: 'suit' must be")


def test_wrong_corner_count(data):
    clearing(data, 2)["corner"] = True
    assert_invalid(data, "expected 4 corner clearings")


def test_corner_not_bool(data):
    clearing(data, 2)["corner"] = "yes"
    assert_invalid(data, "'corner' must be true or false")


def test_asymmetric_adjacency(data):
    clearing(data, 10)["adjacent"].append(1)
    assert_invalid(data, "clearing 10 -> 1 is not symmetric")


def test_unknown_neighbour(data):
    clearing(data, 10)["adjacent"].append(42)
    assert_invalid(data, "unknown clearing 42")


def test_self_adjacent(data):
    clearing(data, 10)["adjacent"].append(10)
    assert_invalid(data, "adjacent to itself")


def test_disconnected(data):
    # 2 з'єднана лише з 1 і 3; розриваємо обидві стежки симетрично
    clearing(data, 2)["adjacent"] = []
    clearing(data, 1)["adjacent"].remove(2)
    clearing(data, 3)["adjacent"].remove(2)
    assert_invalid(data, "not all connected")


def test_bad_center(data):
    clearing(data, 2)["center_mm"] = [10]
    assert_invalid(data, "'center_mm' must be")


def test_bad_radius(data):
    clearing(data, 2)["radius_mm"] = 0
    assert_invalid(data, "'radius_mm' must be a positive number")


def test_reports_all_errors(data):
    clearing(data, 2)["suit"] = "cat"
    clearing(data, 3)["radius_mm"] = -1
    with pytest.raises(BoardError) as exc:
        parse_board(data)
    assert "clearing 2" in str(exc.value)
    assert "clearing 3" in str(exc.value)


# --- create_board ---


def assert_fatal(map_file, message):
    with pytest.raises(FatalError, match=message) as exc:
        create_board({"board": {"map_file": str(map_file)}})
    assert exc.type is FatalError


def test_create_board_missing_file(tmp_path):
    assert_fatal(tmp_path / "missing.yaml", "cannot load the map")


def test_create_board_invalid_map(tmp_path, data):
    clearing(data, 10)["adjacent"].append(42)
    map_file = tmp_path / "map.yaml"
    map_file.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert_fatal(map_file, "unknown clearing 42")
