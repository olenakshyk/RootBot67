"""XArm з фейковим XArmAPI: справжня рука й SDK не потрібні."""

import sys

import pytest

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import load_config
from rootbot.robot import arm as arm_module
from rootbot.robot.arm import ArmError, MockArm, XArm, create_arm

START = [20.0, 10.0, -30.0, 130.0, 30.0, 120.0, 90.0]


class FakeAPI:
    def __init__(self, error_code=0, fail=()):
        self.error_code, self.warn_code = error_code, 0
        self.fail = set(fail)  # назви команд, що повертають ненульовий код
        self.calls = []
        self.angles = list(START)
        self.connected = True
        self.disconnected = False

    def _code(self, name):
        return 1 if name in self.fail else 0

    def motion_enable(self, enable):
        self.calls.append(("motion_enable", enable))
        return self._code("motion_enable")

    def set_mode(self, mode):
        self.calls.append(("set_mode", mode))
        return self._code("set_mode")

    def set_state(self, state):
        self.calls.append(("set_state", state))
        return self._code("set_state")

    def get_servo_angle(self, is_radian):
        return self._code("get_servo_angle"), self.angles + [0.0]  # SDK дає ще й 8-й

    def set_servo_angle(self, angle, speed, mvacc, wait, is_radian):
        self.calls.append(("set_servo_angle", angle, speed, wait))
        if "set_servo_angle" in self.fail:
            self.error_code = 31  # напр. зіткнення
            return 1
        self.angles = list(angle)
        return 0

    def get_position(self, is_radian):
        return self._code("get_position"), [431.2, 23.3, 701.9, -176.1, 18.2, -83.2]

    def get_inverse_kinematics(self, pose, input_is_radian, return_is_radian):
        if "get_inverse_kinematics" in self.fail:
            return 1, None
        return 0, [p / 10 for p in pose] + [0.0]

    def disconnect(self):
        self.disconnected = True


def xarm(api, **kw):
    return XArm(api, speed_deg_s=20, acc_deg_s2=200, **kw)


def test_enables_motion_in_position_mode():
    api = FakeAPI()
    xarm(api)
    assert api.calls == [("motion_enable", True), ("set_mode", 0), ("set_state", 0)]


def test_controller_error_at_start():
    with pytest.raises(ArmError, match="error 22"):
        xarm(FakeAPI(error_code=22))


def test_joints_are_seven():
    assert xarm(FakeAPI()).joints() == START


def test_move_joints_waits_with_default_or_given_speed():
    api = FakeAPI()
    arm = xarm(api)
    target = [0.0] * 7
    arm.move_joints(target)
    arm.move_joints(START, speed_deg_s=10)
    moves = [c for c in api.calls if c[0] == "set_servo_angle"]
    assert moves == [("set_servo_angle", target, 20, True), ("set_servo_angle", START, 10, True)]
    assert arm.joints() == START


def test_failed_move_is_error_with_controller_code():
    arm = xarm(FakeAPI(fail={"set_servo_angle"}))
    with pytest.raises(ArmError, match="set_servo_angle failed: code 1, controller error 31"):
        arm.move_joints([0.0] * 7)


def test_wrong_number_of_joints():
    with pytest.raises(ValueError, match="7 joint angles"):
        xarm(FakeAPI()).move_joints([0.0] * 6)


def test_home_without_taught_pose_stays():
    api = FakeAPI()
    xarm(api).home()
    assert not any(c[0] == "set_servo_angle" for c in api.calls)


def test_home_to_taught_pose():
    api = FakeAPI()
    xarm(api, home_joints=[1.0] * 7).home()
    assert api.angles == [1.0] * 7


def test_freedrive_on_and_off():
    api = FakeAPI()
    arm = xarm(api)
    api.calls.clear()
    arm.freedrive(True)
    arm.freedrive(False)
    assert api.calls == [("set_mode", 2), ("set_state", 0), ("set_mode", 0), ("set_state", 0)]


def test_tcp_pose_and_ik():
    arm = xarm(FakeAPI())
    assert arm.tcp_pose() == [431.2, 23.3, 701.9, -176.1, 18.2, -83.2]
    assert arm.inverse_kinematics([10.0] * 6) == [1.0] * 6 + [0.0]  # 7 кутів


def test_no_ik_is_error():
    with pytest.raises(ArmError, match="get_inverse_kinematics failed"):
        xarm(FakeAPI(fail={"get_inverse_kinematics"})).inverse_kinematics([0.0] * 6)


def test_mock_arm_remembers_joints():
    arm = MockArm()
    arm.move_joints(START)
    assert arm.joints() == START


def test_created_from_config(monkeypatch, no_real_arm):
    api = FakeAPI()
    monkeypatch.setattr(no_real_arm, "XArmAPI", lambda ip, is_radian: api)
    cfg = load_config()
    cfg["robot"].update(mock=False, ip="192.168.1.201", home_joints_deg=START)
    arm = create_arm(cfg)
    assert isinstance(arm, XArm) and arm.speed_deg_s == cfg["robot"]["joint_speed_deg_s"]
    assert arm.home_joints == START


def test_not_connected_is_temporary(monkeypatch, no_real_arm):
    api = FakeAPI()
    api.connected = False
    monkeypatch.setattr(no_real_arm, "XArmAPI", lambda ip, is_radian: api)
    cfg = load_config()
    cfg["robot"]["mock"] = False
    with pytest.raises(TemporaryError, match="does not answer"):
        create_arm(cfg)


def test_controller_error_at_create_is_temporary_and_disconnects(monkeypatch, no_real_arm):
    api = FakeAPI(error_code=22)
    monkeypatch.setattr(no_real_arm, "XArmAPI", lambda ip, is_radian: api)
    cfg = load_config()
    cfg["robot"]["mock"] = False
    with pytest.raises(TemporaryError, match="xArm Studio"):
        create_arm(cfg)
    assert api.disconnected


def test_without_sdk_is_fatal(monkeypatch):
    monkeypatch.setitem(sys.modules, "xarm.wrapper", None)  # import -> ImportError
    cfg = load_config()
    cfg["robot"]["mock"] = False
    with pytest.raises(FatalError, match="xarm-python-sdk"):
        create_arm(cfg)


def test_mock_from_config():
    cfg = load_config()
    cfg["robot"]["mock"] = True
    assert isinstance(arm_module.create_arm(cfg), MockArm)
