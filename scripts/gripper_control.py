"""Ручне керування гріппером (для налагодження). Тип гріппера — з config (robot.gripper).

    python scripts/gripper_control.py                 # показати позицію
    python scripts/gripper_control.py --set 0.25      # стиснути до 25%
    python scripts/gripper_control.py --set 1.0       # відкрити

IP береться з config (robot.ip). Який гріппер (mock/ufactory/somatic) — теж з config.
Деталі: src/rootbot/robot/gripper.py.
"""

import argparse
import logging

from rootbot.config import load_config
from rootbot.robot.gripper import create_gripper


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--set", type=float, default=None, metavar="0.0..1.0", help="виставити позицію")
    args = ap.parse_args()

    with create_gripper(load_config()) as gripper:
        if args.set is not None:
            gripper.set_position(args.set)
            print(f"виставлено {args.set:.3f}")
        print(f"позиція: {gripper.get_position():.3f}")


if __name__ == "__main__":
    main()
