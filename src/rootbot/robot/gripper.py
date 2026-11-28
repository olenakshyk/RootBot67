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

_SOMATIC_MODULE = "rootbot.robot.gripper_somatic"


class Gripper(Protocol):
    """Що вміє будь-який гріппер (mock, ufactory, somatic). Розширюється разом з Manipulator."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def __enter__(self) -> Gripper: ...

    def __exit__(self, *exc: object) -> None: ...

    def get_position(self) -> float: ...

    def set_position(self, position: float) -> float: ...


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
            raise FatalError(f"gripper position must be within 0.0..1.0, got {position}")
        self._position = position
        log.info("MockGripper: set_position %.3f", position)
        return position


class UFactoryGripper:
    """Стандартний гріппер UFACTORY xArm через офіційний SDK (xarm-python-sdk).

    Позиція 0.0 (закрито) .. 1.0 (відкрито); SDK оперує «пульсами» 0..max_pulse, ми масштабуємо.
    Числа заліза (max_pulse, speed_rpm, timeout_s) — з config robot.ufactory_gripper.
    SDK — опційна залежність (extra `robot`): pip install 'rootbot[robot]'.
    """

    def __init__(self, ip: str, max_pulse: int, speed_rpm: int, timeout_s: float) -> None:
        self._ip = ip
        self._max_pulse = max_pulse
        self._speed_rpm = speed_rpm
        self._timeout_s = timeout_s
        self._arm = None

    def connect(self) -> None:
        """Під'єднується й вмикає гріппер у режимі позиціонування.

        Помилку контролера (після зіткнення чи виходу за межі безпеки) скидає людина свідомо;
        поки вона є, SDK повертає ненульовий код і connect() кидає TemporaryError.
        """
        if self._arm is not None:
            return
        try:
            from xarm.wrapper import XArmAPI
        except ImportError as exc:
            raise FatalError(
                "ufactory gripper needs xarm-python-sdk: pip install 'rootbot[robot]'"
            ) from exc
        try:
            arm = XArmAPI(self._ip)
        except Exception as exc:  # SDK кидає власні типи — зводимо до нашої помилки
            raise TemporaryError(f"ufactory gripper: cannot connect to {self._ip} ({exc})") from exc
        try:
            self._require_ok(arm.set_gripper_mode(0), "set_gripper_mode")  # 0 — позиціонування
            self._require_ok(arm.set_gripper_enable(True), "set_gripper_enable")
            self._require_ok(arm.set_gripper_speed(self._speed_rpm), "set_gripper_speed")
        except Exception as exc:
            arm.disconnect()
            if isinstance(exc, TemporaryError):
                raise
            raise TemporaryError(f"ufactory gripper: cannot enable on {self._ip} ({exc})") from exc
        self._arm = arm
        log.info("UFactoryGripper: під'єднано до %s", self._ip)

    @staticmethod
    def _require_ok(code: int, call: str) -> None:
        if code != 0:
            raise TemporaryError(f"ufactory gripper: {call} failed (code={code})")

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
        """Позиція 0.0..1.0; значення поза 0..max_pulse (буває трохи нижче 0) обрізаються."""
        if self._arm is None:
            raise TemporaryError("ufactory gripper: not connected (call connect() first)")
        ret = self._arm.get_gripper_position()
        if isinstance(ret, int):
            _, err = self._arm.get_gripper_err_code()
            raise TemporaryError(
                f"ufactory gripper: fault while reading position (code={ret}, gripper error={err})"
            )
        code, pos = ret
        if code != 0 or pos is None:
            raise TemporaryError(f"ufactory gripper: cannot read position (code={code})")
        return min(max(pos / self._max_pulse, 0.0), 1.0)

    def set_position(self, position: float) -> float:
        if not 0.0 <= position <= 1.0:
            raise FatalError(f"gripper position must be within 0.0..1.0, got {position}")
        if self._arm is None:
            raise TemporaryError("ufactory gripper: not connected (call connect() first)")
        pulse = round(position * self._max_pulse)
        code = self._arm.set_gripper_position(pulse, wait=True, timeout=self._timeout_s)
        if code != 0:
            raise TemporaryError(f"ufactory gripper: cannot set position (code={code})")
        return position


def create_gripper(cfg: dict) -> Gripper:
    """Обирає гріппер за config.

    robot.mock: true          -> MockGripper (пріоритет, як і для руки)
    robot.gripper: mock       -> MockGripper
    robot.gripper: ufactory   -> UFactoryGripper(robot.ip, robot.ufactory_gripper)
    robot.gripper: somatic    -> SomaticGripper(robot.ip)  (приватний gripper_somatic.py)
    """
    robot = cfg["robot"]
    kind = "mock" if robot.get("mock", False) else robot.get("gripper", "mock")

    if kind == "mock":
        return MockGripper()

    ip = robot.get("ip")
    if not ip or "x" in str(ip):
        raise FatalError(f"robot.ip is not set in config/local.yaml for gripper '{kind}'")

    if kind == "ufactory":
        hw = robot["ufactory_gripper"]
        return UFactoryGripper(ip, hw["max_pulse"], hw["speed_rpm"], hw["timeout_s"])

    if kind == "somatic":
        try:
            from rootbot.robot.gripper_somatic import SomaticGripper
        except ModuleNotFoundError as exc:
            if exc.name != _SOMATIC_MODULE:
                raise
            raise FatalError(
                "somatic gripper is not available: gripper_somatic.py is not in this repository"
            ) from exc
        return SomaticGripper(ip)

    raise FatalError(f"unknown robot.gripper: '{kind}' (expected mock | ufactory | somatic)")
