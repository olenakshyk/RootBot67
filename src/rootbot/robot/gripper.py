"""Гріппер руки — захват/розтиск фігурок. Тип обирається з config (robot.gripper).

Варіанти:
  - mock      — без заліза, лише памʼятає позицію й пише в лог (для тестів і розробки без руки);
  - ufactory  — стандартний гріппер UFACTORY xArm через офіційний SDK (xarm-python-sdk);
  - somatic   — гріппер Somatic (приватний драйвер gripper_somatic.py, протокол внутрішній).

Решта коду знає лише Protocol `Gripper`, а не конкретний клас (як і `Arm` в arm.py),
тож будь-який гріппер підставляється без змін в Orchestrator і Manipulator.

    from rootbot.robot.gripper import create_gripper
    with create_gripper(cfg) as g:        # context manager; або connect()/disconnect()
        pos = g.get_position()            # 0.0..1.0
        g.set_position(0.25)              # 0.0 = закрито, 1.0 = відкрито

Позиція скрізь нормалізована в 0.0..1.0. Що таке 0.0/1.0 на конкретному залізі (повне
закриття/відкриття) — звіряти при налаштуванні.
"""

from __future__ import annotations

import logging
from typing import Protocol

from rootbot.common.errors import FatalError, TemporaryError

log = logging.getLogger(__name__)


class Gripper(Protocol):
    """Що вміє будь-який гріппер (mock, ufactory, somatic). Розширюється разом з Manipulator."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_position(self) -> float: ...

    def set_position(self, position: float) -> None: ...


class MockGripper:
    """Гріппер без заліза: лише памʼятає позицію й пише в лог (для тестів і mock-режиму)."""

    def __init__(self) -> None:
        self._position = 0.0

    def connect(self) -> None:
        log.info("MockGripper: connect")

    def disconnect(self) -> None:
        log.info("MockGripper: disconnect")

    def __enter__(self) -> MockGripper:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    def get_position(self) -> float:
        return self._position

    def set_position(self, position: float) -> float:
        if not 0.0 <= position <= 1.0:
            raise FatalError(f"позиція гріппера має бути 0.0..1.0, а не {position}")
        self._position = position
        log.info("MockGripper: set_position %.3f", position)
        return position


class UFactoryGripper:
    """Стандартний гріппер UFACTORY xArm через офіційний SDK (xarm-python-sdk).

    Позиція 0.0 (закрито) .. 1.0 (відкрито); SDK оперує «пульсами» 0..850, ми масштабуємо.
    SDK — опційна залежність (extra `robot`): pip install 'rootbot[robot]'.
    """

    _SDK_MAX = 850  # повний хід гріппера UFACTORY у пульсах SDK

    def __init__(self, ip: str, speed: int = 2000) -> None:
        self._ip = ip
        self._speed = speed
        self._arm = None

    def connect(self) -> None:
        if self._arm is not None:
            return
        try:
            from xarm.wrapper import XArmAPI
        except ImportError as exc:
            raise FatalError(
                "для ufactory-гріппера потрібен xarm-python-sdk: pip install 'rootbot[robot]'"
            ) from exc
        try:
            arm = XArmAPI(self._ip)
            arm.clean_error()
            arm.clean_warn()
            arm.set_gripper_mode(0)  # 0 — режим позиціонування
            arm.set_gripper_enable(True)
            arm.set_gripper_speed(self._speed)
        except Exception as exc:  # SDK кидає власні типи — зводимо до нашої помилки
            raise TemporaryError(f"ufactory-гріппер: не вдалося ініціалізувати {self._ip} ({exc})") from exc
        self._arm = arm
        log.info("UFactoryGripper: під'єднано до %s", self._ip)

    def disconnect(self) -> None:
        if self._arm is not None:
            try:
                self._arm.disconnect()
            finally:
                self._arm = None
                log.info("UFactoryGripper: від'єднано")

    def __enter__(self) -> UFactoryGripper:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    def get_position(self) -> float:
        if self._arm is None:
            raise TemporaryError("ufactory-гріппер: немає з'єднання (спершу connect())")
        code, pos = self._arm.get_gripper_position()
        if code != 0 or pos is None:
            raise TemporaryError(f"ufactory-гріппер: не прочитав позицію (code={code})")
        return pos / self._SDK_MAX

    def set_position(self, position: float) -> float:
        if not 0.0 <= position <= 1.0:
            raise FatalError(f"позиція гріппера має бути 0.0..1.0, а не {position}")
        if self._arm is None:
            raise TemporaryError("ufactory-гріппер: немає з'єднання (спершу connect())")
        code = self._arm.set_gripper_position(round(position * self._SDK_MAX), wait=True)
        if code != 0:
            raise TemporaryError(f"ufactory-гріппер: не виставив позицію (code={code})")
        return position


def create_gripper(cfg: dict) -> Gripper:
    """Обирає гріппер за config.

    robot.mock: true          -> MockGripper (пріоритет, як і для руки)
    robot.gripper: mock       -> MockGripper
    robot.gripper: ufactory   -> UFactoryGripper(robot.ip)
    robot.gripper: somatic    -> SomaticGripper(robot.ip)  (приватний gripper_somatic.py)
    """
    robot = cfg["robot"]
    kind = "mock" if robot.get("mock", False) else robot.get("gripper", "mock")

    if kind == "mock":
        return MockGripper()

    ip = robot.get("ip")
    if not ip or "x" in str(ip):
        raise FatalError(f"не заданий robot.ip у config/local.yaml для гріппера '{kind}'")

    if kind == "ufactory":
        return UFactoryGripper(ip)

    if kind == "somatic":
        try:
            from rootbot.robot.gripper_somatic import SomaticGripper
        except ImportError as exc:
            raise FatalError(
                "gripper: somatic недоступний у цьому репозиторії (gripper_somatic.py не входить у спільний код)"
            ) from exc
        return SomaticGripper(ip)

    raise FatalError(f"невідомий robot.gripper: '{kind}' (очікую mock | ufactory | somatic)")
