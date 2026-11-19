"""PieceDetectorModel без справжньої моделі: переклад виходу детектора й створення з конфігу."""

import numpy as np
import pytest

from rootbot.common.errors import FatalError
from rootbot.common.types import Faction, PieceKind
from rootbot.config import load_config
from rootbot.vision.piece_detector_model import (
    StubPieceDetectorModel,
    create_piece_detector_model,
    parse_classes,
    to_boxes,
)

WARRIOR = (Faction.MARQUISE_BOT, PieceKind.WARRIOR)


def test_raw_output_becomes_piece_boxes_including_the_die():
    xyxy = np.array([[10, 20, 30, 60], [100, 100, 120, 140]], dtype=np.float32)
    cls = np.array([0, 11], dtype=np.float32)
    conf = np.array([0.9, 0.8], dtype=np.float32)
    box, die = to_boxes(xyxy, cls, conf, {0: WARRIOR, 11: (None, PieceKind.DIE)})
    assert (box.x1, box.y2, box.faction, box.kind) == (10, 60, *WARRIOR)
    assert box.confidence == pytest.approx(0.9)
    assert box.base_px() == (20, 60)
    assert (die.faction, die.kind) == (None, PieceKind.DIE)  # кубик теж повертається


def test_default_class_table_parses():
    table = parse_classes(load_config()["vision"]["piece_model"]["classes"])
    assert table["marquise_warrior"] == WARRIOR
    assert table["vagabond"] == (Faction.VAGABOND, PieceKind.PAWN)
    assert table["die"] == (None, PieceKind.DIE)


def test_bad_class_table_is_fatal():
    with pytest.raises(FatalError):
        parse_classes({"x": ["no_such_faction", "warrior"]})


def config(**model):
    cfg = load_config()
    cfg["vision"]["piece_model"].update(model)
    return cfg


def test_factory():
    assert isinstance(create_piece_detector_model(config(type="stub")), StubPieceDetectorModel)
    with pytest.raises(FatalError, match="not set"):
        create_piece_detector_model(config(type="yolo", weights=None))
    with pytest.raises(FatalError, match="no file"):
        create_piece_detector_model(config(type="yolo", weights="no/such/weights.pt"))
    with pytest.raises(FatalError, match="unknown"):
        create_piece_detector_model(config(type="magic"))
