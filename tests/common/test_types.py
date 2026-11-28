from rootbot.common.types import BoardPoint


def test_distance_to():
    assert BoardPoint(0, 0).distance_to(BoardPoint(3, 4)) == 5.0
