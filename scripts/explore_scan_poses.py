"""Оглядові пози без навчання руками: рука сама пробує пози навколо поточної й лишає найкращі.

Постав руку в позу, з якої камера бачить дошку (перевірити: scripts/check_camera.py), і запусти.
Скрипт обчислює кандидатів (зсуви на ±shift, підйом, нахили; camera.explore) і відкидає ті, що
поза robot.workspace_mm. Для кожного — обернена кінематика на контролері, рух на debug-швидкості
з початкової пози, кадр, пошук дошки, назад у початкову: саме цими переходами потім їздить Scanner.
Потім жадібно вибирає до `keep` поз, що разом покривають найбільше дошки, і пише їх у
camera.scan_poses_file. Кадри й підсумок — у data/explore_poses/<час>/.
Після помилки руки чи Ctrl+C рука лишається, де стала (#81): далі руками або в xArm Studio.

    python scripts/explore_scan_poses.py          # кожен рух чекає Enter (s — пропустити позу)
    python scripts/explore_scan_poses.py --yes    # без підтверджень (лише коли вже бачила, як їде)

Перед запуском: safety boundary і collision sensitivity в xArm Studio, TCP payload задано,
рука біля аварійної кнопки, на дошці та навколо вільно.
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from rootbot.config import ROOT, load_config
from rootbot.game.board import create_board
from rootbot.robot.arm import ArmError, XArm, create_arm
from rootbot.robot.scan_poses import candidate_poses, in_workspace, joint_step, select_poses
from rootbot.vision.board_detector import create_board_detector
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.camera import create_camera


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, help="config file instead of config/default.yaml")
    ap.add_argument("--yes", action="store_true", help="do not ask before every move")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(a.config)
    ex = cfg["camera"]["explore"]
    speed = cfg["robot"]["debug_joint_speed_deg_s"]
    out = ROOT / "data" / "explore_poses" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
    out.mkdir(parents=True, exist_ok=True)
    board = create_board(cfg)
    detector = create_board_detector(cfg)
    whole = BoardMask.whole(detector.board_size_mm, cfg["vision"]["mask_mm_per_px"])

    arm = create_arm(cfg)
    if not isinstance(arm, XArm):
        raise SystemExit("robot.mock: true, set it to false for the real arm")
    camera = create_camera(cfg)
    if arm.payload_kg() == 0:
        print("WARNING: TCP payload is 0 kg; set the gripper + camera weight in xArm Studio")
    start_joints, start_tcp = arm.joints(), arm.tcp_pose()
    print(f"start: joints {fmt(start_joints)}, TCP {fmt(start_tcp)}")
    workspace = cfg["robot"]["workspace_mm"]
    if not in_workspace(start_tcp, workspace):
        raise SystemExit(f"the start pose is outside robot.workspace_mm {workspace}")
    max_step = cfg["camera"]["max_joint_step_deg"]

    results: dict[str, dict] = {}
    masks: dict[str, BoardMask] = {}
    try:
        candidates = candidate_poses(
            start_tcp, shift_mm=ex["shift_mm"], lift_mm=ex["lift_mm"], tilt_deg=ex["tilt_deg"]
        )
        for i, (name, tcp) in enumerate(candidates):
            print(f"\n[{i + 1}/{len(candidates)}] {name}: TCP {fmt(tcp)}")
            if not in_workspace(tcp, workspace):
                print(f"  skip: outside robot.workspace_mm {workspace}")
                continue
            try:
                joints = start_joints if name == "start" else arm.inverse_kinematics(tcp)
            except ArmError as e:
                print(f"  skip: no IK ({e})")
                continue
            step = joint_step(joints, start_joints)
            if step > max_step:
                print(f"  skip: a joint would turn {step:.0f} deg (max {max_step})")
                continue
            if not a.yes and input(f"  joints {fmt(joints)}; Enter = move, s = skip: ") == "s":
                continue
            arm.move_joints(start_joints, speed)  # кожен кандидат — від початкової пози
            arm.move_joints(joints, speed)
            time.sleep(cfg["camera"]["settle_s"])
            frame = camera.capture()
            cv2.imwrite(str(out / f"{i:02d}_{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            found = detector.find(frame)
            if found is None:
                print("  board NOT found")
                continue
            mask = whole.visible_on([found])
            seen = [
                int(c.id) for c in board.clearings.values() if found.sees(c.center, c.radius_mm)
            ]
            masks[name] = mask
            results[name] = {
                "name": name,
                "joints": [round(j, 2) for j in arm.joints()],
                "tcp": [round(p, 1) for p in arm.tcp_pose()],
                "inliers": found.inliers,
                "coverage": round(mask.area() / whole.area(), 3),
                "clearings": sorted(seen),
                "sees": [[round(p.x, 1), round(p.y, 1)] for p in found.visible()],
            }
            print(
                f"  board found: inliers {found.inliers}, visible {results[name]['coverage']:.0%}, "
                f"clearings {sorted(seen)}"
            )
        print("\nback to start")
        arm.move_joints(start_joints, speed)
    except BaseException:
        print("\nstopped: the arm stays where it is (move it by hand or in xArm Studio)")
        raise
    finally:
        camera_close = getattr(camera, "close", None)
        if camera_close:
            camera_close()
        arm.close()

    if "start" not in masks:
        raise SystemExit("the board is not seen from the start pose: nothing to build on")
    chosen = select_poses(
        masks, {n: r["clearings"] for n, r in results.items()}, ex["keep"], first="start"
    )
    total = BoardMask.empty(whole)
    for name in chosen:
        total = total | masks[name]
    print(f"\nchosen {len(chosen)} poses, together {total.area() / whole.area():.0%} of the board:")
    for name in chosen:
        r = results[name]
        print(f"  {name}: visible {r['coverage']:.0%}, clearings {r['clearings']}")

    data = {
        "source": "scripts/explore_scan_poses.py",
        "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "poses": [results[name] for name in chosen],
    }
    (out / "all_poses.yaml").write_text(yaml.safe_dump(list(results.values()), sort_keys=False))
    path = ROOT / cfg["camera"]["scan_poses_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"saved: {path}\nframes: {out}")


def fmt(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:.1f}" for v in values) + "]"


if __name__ == "__main__":
    main()
