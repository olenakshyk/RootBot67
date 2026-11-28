"""Перевірка руки: підключення, стан, поточні кути; за бажанням — перший маленький рух.

    python scripts/check_arm.py                  # лише прочитати (рука не рухається)
    python scripts/check_arm.py --nudge 7 5      # суглоб 7 на +5°, потім назад (debug-швидкість)
    python scripts/check_arm.py --freedrive      # вести руку руками; Enter — надрукувати кути

Перед першим рухом: safety boundary і collision sensitivity в xArm Studio, рука біля аварійної
кнопки. Кожен рух чекає Enter.
"""

import argparse
import logging
from pathlib import Path

from rootbot.config import load_config
from rootbot.robot.arm import XArm, create_arm


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, help="config file instead of config/default.yaml")
    ap.add_argument(
        "--nudge", nargs=2, type=float, metavar=("JOINT", "DEG"), help="move joint 1..7 and back"
    )
    ap.add_argument("--freedrive", action="store_true", help="move the arm by hand, print joints")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(a.config)
    arm = create_arm(cfg)
    if not isinstance(arm, XArm):
        print("robot.mock: true, nothing to check")
        return
    try:
        describe(arm)
        if a.nudge:
            nudge(arm, int(a.nudge[0]), a.nudge[1], cfg["robot"]["debug_joint_speed_deg_s"])
        if a.freedrive:
            freedrive(arm)
    finally:
        arm.close()


def describe(arm: XArm) -> None:
    api = arm.api
    print(f"xArm {api.version}, axis {api.axis}")
    print(f"  state {api.state}, mode {api.mode}, error {api.error_code}, warning {api.warn_code}")
    print(f"  collision sensitivity {api.collision_sensitivity} (0 = collision detection off)")
    print(f"  joints (deg): {fmt(arm.joints())}")
    print(f"  TCP (mm, deg): {fmt(api.position)}")


def nudge(arm: XArm, joint: int, deg: float, speed: float) -> None:
    """Суглоб `joint` (1..7) на `deg` градусів і назад."""
    start = arm.joints()
    target = list(start)
    target[joint - 1] += deg
    print(f"joint {joint}: {start[joint - 1]:.1f} -> {target[joint - 1]:.1f} deg at {speed} deg/s")
    input("Enter = move (Ctrl+C = cancel) ")
    arm.move_joints(target, speed)
    print(f"  now: {fmt(arm.joints())}")
    input("Enter = move back ")
    arm.move_joints(start, speed)
    print(f"  now: {fmt(arm.joints())}")


def freedrive(arm: XArm) -> None:
    """Freedrive: рука вільна; Enter — кути, q + Enter — вийти (рука знову тримає позу)."""
    input("freedrive: hold the arm, then Enter (it will go limp) ")
    arm.freedrive(True)
    try:
        while input("Enter = print joints, q = quit: ").strip().lower() != "q":
            print(f"  joints: {fmt(arm.joints())}")
    finally:
        arm.freedrive(False)


def fmt(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:.1f}" for v in values) + "]"


if __name__ == "__main__":
    main()
