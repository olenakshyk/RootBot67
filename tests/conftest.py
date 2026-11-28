"""Спільне для всіх тестів: жоден тест не підключається до справжньої руки.

config/local.yaml може містити справжню IP руки, а load_config() його читає. Тому xArm SDK
підмінено модулем, чий XArmAPI падає; тести руки ставлять свій фейк (tests/robot/test_arm.py).
"""

import sys
from types import ModuleType

import pytest


def _real_arm_forbidden(*args, **kwargs):
    raise AssertionError("tests must not connect to the real xArm")


@pytest.fixture(autouse=True)
def no_real_arm(monkeypatch):
    wrapper = ModuleType("xarm.wrapper")
    wrapper.XArmAPI = _real_arm_forbidden  # type: ignore[attr-defined]
    xarm = ModuleType("xarm")
    xarm.wrapper = wrapper  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xarm", xarm)
    monkeypatch.setitem(sys.modules, "xarm.wrapper", wrapper)
    return wrapper
