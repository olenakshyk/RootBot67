"""Збір кадрів для датасету фігурок (#49): рука об'їжджає оглядові пози, з кожної кадр з описом.

Пози — з бібліотеки camera.scan_poses_file (scripts/explore_scan_poses.py). Розстав фігурки й
запусти; після кожного кола скрипт чекає Enter (переставити фігурки й зняти ще коло) або q.
Кадри й опис — у data/raw/<час>/: r<коло>_<поза>.jpg і r<коло>_<поза>.yaml (поза, кути суглобів,
причина, чи знайдено дошку, inliers, видимі галявини, час). Наприкінці рука повертається в
початкову позу.

    python scripts/capture_dataset.py                          # усі пози, коло за колом
    python scripts/capture_dataset.py --poses start tilt_roll+12
    python scripts/capture_dataset.py --yes                    # без підтвердження першого руху

Перед запуском — як для explore_scan_poses.py: safety boundary, рука біля аварійної кнопки.
"""

import argparse
import itertools
import logging
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from rootbot.config import ROOT, load_config
from rootbot.game.board import create_board
from rootbot.robot.arm import create_arm
from rootbot.robot.scan import create_scanner
from rootbot.vision.board_detector import create_board_detector
from rootbot.vision.camera import create_camera


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, help="config file instead of config/default.yaml")
    ap.add_argument("--poses", nargs="+", help="pose names from the library (default: all)")
    ap.add_argument("--yes", action="store_true", help="do not ask before the first move")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(a.config)
    speed = cfg["robot"]["debug_joint_speed_deg_s"]
    out = ROOT / "data" / "raw" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
    board = create_board(cfg)
    detector = create_board_detector(cfg)

    arm = create_arm(cfg)
    camera = create_camera(cfg)
    scanner = create_scanner(cfg, arm, camera)
    if not scanner.poses:
        raise SystemExit("no scan pose library: run scripts/explore_scan_poses.py first")
    unknown = set(a.poses or []) - {p.name for p in scanner.poses}
    if unknown:
        raise SystemExit(f"no such poses in the library: {sorted(unknown)}")
    poses = [p for p in scanner.poses if a.poses is None or p.name in a.poses]
    print(f"poses: {[p.name for p in poses]}\nframes: {out}")
    if not a.yes and input("Enter = start moving, q = quit: ") == "q":
        return

    out.mkdir(parents=True, exist_ok=True)
    try:
        for n in itertools.count(1):
            for pose in poses:
                frame = scanner.shoot(pose, speed)
                found = detector.find(frame)
                seen = [
                    int(c.id)
                    for c in board.clearings.values()
                    if found is not None and found.sees(c.center, c.radius_mm)
                ]
                name = f"r{n:03d}_{pose.name}"
                cv2.imwrite(str(out / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                meta = {
                    "pose": pose.name,
                    "joints": [round(j, 2) for j in arm.joints()],
                    "reason": "dataset",
                    "board_found": found is not None,
                    "inliers": found.inliers if found is not None else 0,
                    "clearings": sorted(seen),
                    "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
                }
                (out / f"{name}.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
                status = f"inliers {meta['inliers']}" if found is not None else "board NOT found"
                print(f"  {name}: {status}, clearings {meta['clearings']}")
            if input(f"round {n} done; rearrange pieces, Enter = next round, q = quit: ") == "q":
                break
    finally:
        print("back to start")
        scanner.shoot(scanner.poses[0], speed)
        camera_close = getattr(camera, "close", None)
        if camera_close:
            camera_close()
        arm_close = getattr(arm, "close", None)
        if arm_close:
            arm_close()


if __name__ == "__main__":
    main()
