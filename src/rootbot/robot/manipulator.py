"""Manipulator — перенесення однієї фігурки рукою.

Зовнішній опис: docs/architecture.md -> robot -> Manipulator.

Вхід — задача PieceMoveTask: звідки взяти й куди поставити одну фігурку.

Кроки: перевірити, що обидві точки в робочій зоні (до руху) -> над source на безпечній висоті ->
кадр зблизька: знайти саме фігурку бота (source — приблизна точка, напр. клітинка доріжки рахунку,
де можуть стояти й чужі жетони), уточнити її позицію й поворот пальців -> опуститися, взяти
(закрити гріппер) -> піднятися -> над target -> опуститися, поставити (відкрити гріппер) ->
піднятися. Траєкторія й висоти: docs/robot.md.

Поки заглушка: лише пише задачу в лог, рука й гріппер не рухаються.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from rootbot.common.types import BoardPoint
from rootbot.robot.arm import Arm
from rootbot.robot.gripper import Gripper

log = logging.getLogger(__name__)

SUPPLY: Literal["supply"] = "supply"  # запас на планшеті фракції бота (config supply)
Location = BoardPoint | Literal["supply"]


@dataclass(frozen=True)
class PieceMoveTask:
    """Перенести одну фігурку."""

    source: Location  # звідки взяти: мм у системі дошки, або SUPPLY
    target: Location  # куди поставити: мм у системі дошки, або SUPPLY


class Manipulator:
    def __init__(self, arm: Arm, gripper: Gripper) -> None:
        self.arm = arm
        self.gripper = gripper

    def move(self, task: PieceMoveTask) -> None:
        log.info(f"Manipulator: {task.source} -> {task.target} (motion not implemented)")


def create_manipulator(cfg: dict, arm: Arm, gripper: Gripper) -> Manipulator:
    return Manipulator(arm, gripper)
