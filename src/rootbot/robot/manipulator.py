"""Manipulator — перенесення однієї фігурки рукою.

Зовнішній опис: docs/architecture.md -> robot -> Manipulator.

Вхід — задача PieceMoveTask: звідки взяти й куди поставити одну фігурку.

Кроки: перевірити, що обидві точки в робочій зоні (до руху) -> над source на безпечній висоті ->
кадр зблизька, уточнити позицію й поворот пальців -> опуститися, взяти -> піднятися -> над target ->
опуститися, поставити -> піднятися. Траєкторія й висоти: docs/robot.md.

Поки заглушка: лише пише задачу в лог, рука не рухається.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from rootbot.common.types import BoardPoint
from rootbot.robot.arm import Arm

log = logging.getLogger(__name__)

SUPPLY: Literal["supply"] = "supply"  # запас на планшеті фракції бота (config supply)
Location = BoardPoint | Literal["supply"]


@dataclass(frozen=True)
class PieceMoveTask:
    """Перенести одну фігурку."""

    source: Location  # звідки взяти: мм у системі дошки, або SUPPLY
    target: Location  # куди поставити: мм у системі дошки, або SUPPLY


class Manipulator:
    def __init__(self, arm: Arm) -> None:
        self.arm = arm

    def move(self, task: PieceMoveTask) -> None:
        log.info(f"Manipulator: {task.source} -> {task.target} (motion not implemented)")


def create_manipulator(cfg: dict, arm: Arm) -> Manipulator:
    return Manipulator(arm)
