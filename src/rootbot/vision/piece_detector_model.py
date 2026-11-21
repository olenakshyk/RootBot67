"""PieceDetectorModel — модель фігурок: кадри -> рамки (PieceBox). Detector знає лише цей інтерфейс.

Вимоги: docs/requirements/detector.md (FR-V10..V12, NFR-V2).

Модель створюється один раз на старті (main -> create_piece_detector_model) і живе до кінця
програми: ваги завантажуються й модель прогрівається одразу, на огляд — лише передбачення.
Усі кадри огляду йдуть одним викликом (пакетом). Важкі залежності (torch, ultralytics)
імпортуються лише в реалізації.

Реалізацію обирає config vision.piece_model.type: stub (нічого не знаходить) | yolo.
Класи моделі -> (фракція, вид) — vision.piece_model.classes, за НАЗВОЮ класу в моделі: та сама
таблиця підходить моделям з різною нумерацією класів. Модель віддає все, що бачить (і кубик, у якого
фракції немає — None); що з цим робити, вирішує Detector. Кожен клас моделі мусить бути в таблиці,
інакше FatalError на старті — щоб нічого не губилося непомітно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from rootbot.common.errors import FatalError
from rootbot.common.types import Faction, PieceKind
from rootbot.config import ROOT
from rootbot.vision.camera import Frame

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PieceBox:
    """Одна знайдена річ на кадрі: фігурка, жетон, маркер рахунку, кубик."""

    x1: float  # рамка в пікселях кадру
    y1: float
    x2: float
    y2: float
    faction: Faction | None  # None — нічиє (кубик)
    kind: PieceKind
    confidence: float  # 0..1

    def base_px(self) -> tuple[float, float]:
        """Точка основи фігурки на кадрі: середина нижнього краю рамки.
        Поправка на висоту фігурки й нахил камери — згодом (vision/geometry.py)."""
        return (self.x1 + self.x2) / 2, self.y2


class PieceDetectorModel(Protocol):
    def detect(self, frames: list[Frame]) -> list[list[PieceBox]]:
        """Рамки для кожного кадру (той самий порядок, що й frames)."""
        ...


class StubPieceDetectorModel:
    """Заглушка: нічого не знаходить. Замінити справжньою моделлю (YOLO тощо)."""

    def __init__(self) -> None:
        # один раз на старті, а не на кожен огляд — інакше засмічує лог
        log.warning("PieceDetectorModel: stub, no pieces will be detected")

    def detect(self, frames: list[Frame]) -> list[list[PieceBox]]:
        return [[] for _ in frames]


ClassTable = dict[str, tuple[Faction | None, PieceKind]]  # назва класу моделі -> (фракція, вид)


class YoloPieceDetectorModel:
    """YOLO (ultralytics). Ваги завантажуються й модель прогрівається при створенні."""

    def __init__(self, weights: Path, device: str, classes: ClassTable, min_confidence: float):
        import torch
        from ultralytics import YOLO

        if device.startswith("cuda") and not torch.cuda.is_available():
            log.warning("PieceDetectorModel: CUDA is not available, running on CPU (slower)")
            device = "cpu"
        self.device = device
        self.min_confidence = min_confidence
        self._model: Any = YOLO(str(weights))
        names: dict[int, str] = self._model.names
        missing = sorted(name for name in names.values() if name not in classes)
        if missing:
            raise FatalError(f"vision.piece_model.classes: no entry for model classes {missing}")
        self._classes = {i: classes[name] for i, name in names.items()}
        if device != "cpu":
            # перший виклик на GPU повільний (ініціалізація CUDA) — нехай це буде на старті
            self.detect([np.zeros((64, 64, 3), dtype=np.uint8)])

    def detect(self, frames: list[Frame]) -> list[list[PieceBox]]:
        if not frames:
            return []
        results = self._model.predict(
            frames, device=self.device, conf=self.min_confidence, verbose=False
        )
        return [
            to_boxes(
                r.boxes.xyxy.cpu().numpy(),
                r.boxes.cls.cpu().numpy(),
                r.boxes.conf.cpu().numpy(),
                self._classes,
            )
            for r in results
        ]


def to_boxes(
    xyxy: npt.NDArray[np.float32],
    cls: npt.NDArray[np.float32],
    conf: npt.NDArray[np.float32],
    classes: dict[int, tuple[Faction | None, PieceKind]],
) -> list[PieceBox]:
    """Сирий вихід детектора (рамки, номери класів, впевненість) -> PieceBox."""
    return [
        PieceBox(float(x1), float(y1), float(x2), float(y2), *classes[int(c)], float(p))
        for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf, strict=True)
    ]


def parse_classes(raw: dict[str, list[str | None]]) -> ClassTable:
    """config vision.piece_model.classes: {назва класу: [фракція або null, вид]} -> ClassTable."""
    try:
        return {
            name: (None if f is None else Faction(f), PieceKind(k)) for name, (f, k) in raw.items()
        }
    except (ValueError, TypeError) as e:
        raise FatalError(f"vision.piece_model.classes: {e}") from e


def create_piece_detector_model(cfg: dict) -> PieceDetectorModel:
    m = cfg["vision"]["piece_model"]
    kind = m["type"]
    if kind == "stub":
        return StubPieceDetectorModel()
    if kind == "yolo":
        if not m["weights"]:
            raise FatalError("vision.piece_model.weights: not set (no trained model yet)")
        weights = ROOT / m["weights"]
        if not weights.exists():
            raise FatalError(f"vision.piece_model.weights: no file {weights}")
        classes = parse_classes(m["classes"])
        return YoloPieceDetectorModel(
            weights, m["device"], classes, cfg["vision"]["min_confidence"]
        )
    raise FatalError(f"vision.piece_model.type: unknown value {kind!r} (stub | yolo)")
