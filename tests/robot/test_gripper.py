"""Спільний тест гріппера: Mock, вибір типу з конфігу. Без заліза і без xArm SDK."""

import pytest

from rootbot.common.errors import FatalError
from rootbot.robot import gripper as g


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
    assert isinstance(g.create_gripper({"robot": {"mock": True, "ip": "192.168.1.200"}}), g.MockGripper)
    assert isinstance(g.create_gripper({"robot": {"gripper": "mock"}}), g.MockGripper)


def test_create_ufactory():
    # UFactoryGripper створюється без SDK (SDK потрібен лише на connect())
    grip = g.create_gripper({"robot": {"gripper": "ufactory", "ip": "192.168.1.200"}})
    assert isinstance(grip, g.UFactoryGripper)


def test_real_gripper_needs_valid_ip():
    with pytest.raises(FatalError):
        g.create_gripper({"robot": {"gripper": "ufactory", "ip": "192.168.1.xxx"}})


def test_unknown_gripper_type_rejected():
    with pytest.raises(FatalError):
        g.create_gripper({"robot": {"gripper": "nonsense", "ip": "192.168.1.200"}})
