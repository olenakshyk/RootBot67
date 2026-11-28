"""Спільний тест гріппера: Mock, UFactory з фейковим SDK, вибір типу з конфігу. Без заліза."""

import builtins
import sys
import types

import pytest

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.robot import gripper as g

UFACTORY_HW = {"max_pulse": 850, "speed_rpm": 2000, "timeout_s": 10}
UFACTORY_CFG = {
    "robot": {"gripper": "ufactory", "ip": "192.168.1.200", "ufactory_gripper": UFACTORY_HW}
}


class FakeXArmAPI:
    """Фейк xarm.wrapper.XArmAPI: пише виклики й повертає задані коди."""

    codes: dict = {}
    position: object = (0, 425)
    instances: list = []

    def __init__(self, ip: str) -> None:
        self.calls: list = []
        FakeXArmAPI.instances.append(self)

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "get_gripper_position":
                return FakeXArmAPI.position
            if name == "get_gripper_err_code":
                return 0, 23
            return FakeXArmAPI.codes.get(name, 0)

        return call


@pytest.fixture
def fake_sdk(monkeypatch):
    monkeypatch.setattr(FakeXArmAPI, "codes", {})
    monkeypatch.setattr(FakeXArmAPI, "position", (0, 425))
    monkeypatch.setattr(FakeXArmAPI, "instances", [])
    wrapper = types.ModuleType("xarm.wrapper")
    wrapper.XArmAPI = FakeXArmAPI
    monkeypatch.setitem(sys.modules, "xarm", types.ModuleType("xarm"))
    monkeypatch.setitem(sys.modules, "xarm.wrapper", wrapper)
    return FakeXArmAPI


def _ufactory() -> g.UFactoryGripper:
    return g.UFactoryGripper("192.168.1.200", max_pulse=850, speed_rpm=1500, timeout_s=7)


def test_mock_remembers_position():
    mock = g.MockGripper()
    assert mock.set_position(0.5) == 0.5
    assert mock.get_position() == 0.5


def test_mock_out_of_range_rejected():
    mock = g.MockGripper()
    for bad in (-0.1, 1.1):
        with pytest.raises(FatalError):
            mock.set_position(bad)


def test_create_mock_via_flag_and_type():
    assert isinstance(
        g.create_gripper({"robot": {"mock": True, "ip": "192.168.1.200"}}), g.MockGripper
    )
    assert isinstance(g.create_gripper({"robot": {"gripper": "mock"}}), g.MockGripper)


def test_create_ufactory():
    # UFactoryGripper створюється без SDK (SDK потрібен лише на connect())
    grip = g.create_gripper(UFACTORY_CFG)
    assert isinstance(grip, g.UFactoryGripper)


def test_create_ufactory_reads_hardware_numbers_from_config(fake_sdk):
    hw = {"max_pulse": 800, "speed_rpm": 3000, "timeout_s": 5}
    cfg = {"robot": {"gripper": "ufactory", "ip": "192.168.1.200", "ufactory_gripper": hw}}
    with g.create_gripper(cfg) as grip:
        grip.set_position(0.5)
    calls = fake_sdk.instances[0].calls
    assert ("set_gripper_speed", (3000,), {}) in calls
    assert ("set_gripper_position", (400,), {"wait": True, "timeout": 5}) in calls


def test_ufactory_connect_enables_gripper_and_keeps_controller_errors(fake_sdk):
    with _ufactory():
        pass
    names = [name for name, _, _ in fake_sdk.instances[0].calls]
    assert names == ["set_gripper_mode", "set_gripper_enable", "set_gripper_speed", "disconnect"]


def test_ufactory_connect_raises_on_nonzero_code_and_disconnects(fake_sdk):
    fake_sdk.codes = {"set_gripper_enable": 102}
    grip = _ufactory()
    with pytest.raises(TemporaryError, match="set_gripper_enable.*102"):
        grip.connect()
    names = [name for name, _, _ in fake_sdk.instances[0].calls]
    assert names == ["set_gripper_mode", "set_gripper_enable", "disconnect"]
    with pytest.raises(TemporaryError):
        grip.get_position()


def test_ufactory_get_position_bare_fault_code_raises_with_code(fake_sdk):
    fake_sdk.position = 102
    with _ufactory() as grip, pytest.raises(TemporaryError, match="code=102.*gripper error=23"):
        grip.get_position()


def test_ufactory_get_position_clamped_to_unit_range(fake_sdk):
    with _ufactory() as grip:
        fake_sdk.position = (0, -10)
        assert grip.get_position() == 0.0
        fake_sdk.position = (0, 900)
        assert grip.get_position() == 1.0
        fake_sdk.position = (0, 425)
        assert grip.get_position() == 0.5


def test_real_gripper_needs_valid_ip():
    with pytest.raises(FatalError):
        g.create_gripper({"robot": {"gripper": "ufactory", "ip": "192.168.1.xxx"}})


def test_unknown_gripper_type_rejected():
    with pytest.raises(FatalError):
        g.create_gripper({"robot": {"gripper": "nonsense", "ip": "192.168.1.200"}})


def _import_fails_with(monkeypatch, missing: str) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rootbot.robot.gripper_somatic":
            raise ModuleNotFoundError(f"No module named '{missing}'", name=missing)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_somatic_driver_missing_is_fatal(monkeypatch):
    _import_fails_with(monkeypatch, "rootbot.robot.gripper_somatic")
    with pytest.raises(FatalError):
        g.create_gripper({"robot": {"gripper": "somatic", "ip": "192.168.1.200"}})


def test_somatic_driver_missing_dependency_propagates(monkeypatch):
    _import_fails_with(monkeypatch, "some_driver_dependency")
    with pytest.raises(ModuleNotFoundError) as info:
        g.create_gripper({"robot": {"gripper": "somatic", "ip": "192.168.1.200"}})
    assert info.value.name == "some_driver_dependency"
