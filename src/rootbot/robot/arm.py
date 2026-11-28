"""Рука: XArm (справжня, через xArm SDK) і MockArm (без робота, рухи пишуться в лог).

create_arm(cfg) обирає за config: robot.mock.
Решта коду знає лише Protocol Arm, а не конкретний клас,
тож справжня рука підставляється без змін в Orchestrator і Manipulator.

Рухи — за кутами суглобів (градуси), записаними під час навчання (freedrive): передбачувано,
без оберненої кінематики. Перевірити руку й перший рух: scripts/check_arm.py.
"""

import ipaddress
import logging
from typing import Any, Protocol

from rootbot.common.errors import FatalError, TemporaryError

log = logging.getLogger(__name__)

JOINTS = 7  # xArm 7

# xArm SDK: режими й стан (set_mode / set_state)
MODE_POSITION = 0  # звичайні рухи з коду
MODE_FREEDRIVE = 2  # рука вільна, її ведуть руками (навчання поз)
STATE_READY = 0


class ArmError(RuntimeError):
    """Рука не виконала команду: ненульовий код SDK або помилка контролера."""


class Arm(Protocol):
    """Що вміє будь-яка рука (XArm, MockArm, фейк у тестах). Розширюється разом з Manipulator."""

    def home(self) -> None: ...

    def joints(self) -> list[float]: ...

    def move_joints(self, joints: list[float], speed_deg_s: float | None = None) -> None: ...


class MockArm:
    """Рука без робота: нічого не рухає, лише пише в лог і пам'ятає кути."""

    def __init__(self) -> None:
        self._joints = [0.0] * JOINTS

    def home(self) -> None:
        log.info("MockArm: home")

    def joints(self) -> list[float]:
        return list(self._joints)

    def move_joints(self, joints: list[float], speed_deg_s: float | None = None) -> None:
        log.info(f"MockArm: move_joints {joints}")
        self._joints = list(joints)


class XArm:
    """Справжня рука через xarm.wrapper.XArmAPI (кути в градусах).

    Кожна команда SDK повертає код; ненульовий — ArmError з кодом помилки контролера.
    home_joints None — додому не їздити (поза ще не навчена): краще стояти, ніж їхати навмання.
    """

    def __init__(
        self,
        api: Any,
        *,
        speed_deg_s: float,
        acc_deg_s2: float,
        home_joints: list[float] | None = None,
    ) -> None:
        self.api = api
        self.speed_deg_s = speed_deg_s
        self.acc_deg_s2 = acc_deg_s2
        self.home_joints = home_joints
        if api.error_code:
            raise ArmError(
                f"xArm controller error {api.error_code}: clear it in xArm Studio and retry"
            )
        self._call("motion_enable", enable=True)
        self._set_mode(MODE_POSITION)

    def joints(self) -> list[float]:
        """Поточні кути суглобів, градуси."""
        angles = self._call("get_servo_angle", is_radian=False)
        return [float(a) for a in angles[:JOINTS]]

    def tcp_pose(self) -> list[float]:
        """Поточна поза кінця руки: x, y, z (мм), roll, pitch, yaw (градуси), система основи."""
        pose = self._call("get_position", is_radian=False)
        return [float(p) for p in pose[:6]]

    def inverse_kinematics(self, pose: list[float]) -> list[float]:
        """Кути суглобів для пози кінця руки (обчислює контролер, рука не рухається)."""
        angles = self._call(
            "get_inverse_kinematics", pose, input_is_radian=False, return_is_radian=False
        )
        return [float(a) for a in angles[:JOINTS]]

    def payload_kg(self) -> float:
        """Вантаж на кінці руки з налаштувань контролера (TCP payload), кг."""
        return float(self.api.tcp_load[0])

    def move_joints(self, joints: list[float], speed_deg_s: float | None = None) -> None:
        """Рух у кути суглобів (градуси); повертається, коли рука стала."""
        if len(joints) != JOINTS:
            raise ValueError(f"expected {JOINTS} joint angles, got {len(joints)}")
        speed = speed_deg_s or self.speed_deg_s
        log.info(f"XArm: move_joints {[round(j, 1) for j in joints]} at {speed} deg/s")
        self._call(
            "set_servo_angle",
            angle=list(joints),
            speed=speed,
            mvacc=self.acc_deg_s2,
            wait=True,
            is_radian=False,
        )

    def home(self) -> None:
        if self.home_joints is None:
            log.warning("XArm: robot.home_joints_deg is not set, staying in place")
            return
        self.move_joints(self.home_joints)

    def freedrive(self, on: bool) -> None:
        """Freedrive: руку можна вести руками (навчання поз). Вимкнути перед рухами з коду."""
        log.info(f"XArm: freedrive {'on' if on else 'off'}")
        self._set_mode(MODE_FREEDRIVE if on else MODE_POSITION)

    def close(self) -> None:
        self.api.disconnect()

    def _set_mode(self, mode: int) -> None:
        self._call("set_mode", mode)
        self._call("set_state", STATE_READY)

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Виклик SDK з перевіркою коду. Повертає дані, якщо SDK віддає (code, data)."""
        result = getattr(self.api, name)(*args, **kwargs)
        code, data = result if isinstance(result, tuple) else (result, None)
        if code != 0:
            raise ArmError(
                f"xArm {name} failed: code {code}, "
                f"controller error {self.api.error_code}, warning {self.api.warn_code}"
            )
        return data


def create_arm(cfg: dict) -> Arm:
    r = cfg["robot"]
    if r.get("mock", False):
        return MockArm()
    try:
        ipaddress.ip_address(r["ip"])
    except ValueError as e:
        raise FatalError(
            f"robot.ip {r['ip']!r} is not an IP address: set it in config/local.yaml"
        ) from e
    try:
        from xarm.wrapper import XArmAPI  # optional dependency (robot)
    except ImportError as e:
        raise FatalError(
            "xarm-python-sdk is not installed: pip install -e '.[robot]', or set robot.mock: true"
        ) from e
    api = XArmAPI(r["ip"], is_radian=False)
    if not api.connected:
        raise TemporaryError(
            f"xArm at {r['ip']} does not answer: Ethernet cable, laptop IP 192.168.1.x?"
        )
    try:
        return XArm(
            api,
            speed_deg_s=r["joint_speed_deg_s"],
            acc_deg_s2=r["joint_acc_deg_s2"],
            home_joints=r.get("home_joints_deg"),
        )
    except ArmError as e:
        api.disconnect()
        raise TemporaryError(str(e)) from e
