"""Рука: XArm (справжня, через xArm SDK) і MockArm (без робота, рухи пишуться в лог).

create_arm(cfg) обирає за config: robot.mock.
Решта коду знає лише Protocol Arm, а не конкретний клас,
тож справжня рука підставляється без змін в Orchestrator і Manipulator.
"""

import logging
from typing import Protocol

from rootbot.common.errors import FatalError

log = logging.getLogger(__name__)


class Arm(Protocol):
    """Що вміє будь-яка рука (XArm, MockArm, фейк у тестах). Розширюється разом з Manipulator."""

    def home(self) -> None: ...


class MockArm:
    """Рука без робота: нічого не рухає, лише пише в лог."""

    def home(self) -> None:
        log.info("MockArm: home")


def create_arm(cfg: dict) -> Arm:
    if cfg["robot"].get("mock", False):
        return MockArm()
    raise FatalError("real arm (XArm) is not implemented yet: set robot.mock: true")
