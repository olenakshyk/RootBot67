"""Перевірка камери: кадр -> чи знаходить BoardDetector дошку, скільки її видно, які галявини.

Камера та, що в конфігу (camera.source: realsense | folder), тож без камери можна перевірити на теці
з кадрами. Для кожного кадру зберігає сам кадр і накладення (контур дошки, галявини з мапи)
у data/camera_check/<час>/ і друкує підсумок.

    python scripts/check_camera.py                    # 1 кадр
    python scripts/check_camera.py --frames 5         # 5 кадрів через --interval с
    python scripts/check_camera.py --live             # вікно наживо: s — зберегти кадр, q — вийти
    python scripts/check_camera.py --save-intrinsics  # RealSense: intrinsics у camera.yaml

Для RealSense ще друкує модель, USB (потрібен 3.x) і поточні експозицію й баланс білого: їх можна
переписати в config/local.yaml (camera.exposure, camera.white_balance), щоб зафіксувати.
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

from rootbot.common.types import BoardPoint
from rootbot.config import ROOT, load_config
from rootbot.game.board import Board, create_board
from rootbot.vision.board_detector import BoardDetector, BoardHomography, create_board_detector
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.camera import Frame, RealSenseCamera, create_camera

RED, YELLOW, GREEN = (0, 0, 255), (0, 255, 255), (0, 200, 0)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, help="config file instead of config/default.yaml")
    ap.add_argument("--frames", type=int, default=1, help="how many frames to check")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    ap.add_argument("--live", action="store_true", help="live window instead of --frames")
    ap.add_argument("--save-intrinsics", action="store_true", help="RealSense: write camera.yaml")
    ap.add_argument("--out", type=Path, help="where to save frames (default data/camera_check/...)")
    a = ap.parse_args()

    cfg = load_config(a.config)
    out = a.out or ROOT / "data" / "camera_check" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
    out.mkdir(parents=True, exist_ok=True)
    board = create_board(cfg)
    detector = create_board_detector(cfg)
    camera = create_camera(cfg)
    print(f"camera: {cfg['camera']['source']}, frames -> {out}")

    try:
        if isinstance(camera, RealSenseCamera):
            describe(camera, cfg, a.save_intrinsics)
        if a.live:
            live(camera, detector, board, cfg, out)
        else:
            for i in range(a.frames):
                if i:
                    time.sleep(a.interval)
                check(camera.capture(), i, detector, board, cfg, out)
    finally:
        if isinstance(camera, RealSenseCamera):
            camera.close()


def describe(camera: RealSenseCamera, cfg: dict, save_intrinsics: bool) -> None:
    """Що за камера, USB, експозиція; за бажанням — intrinsics у файл."""
    info = camera.info()
    print("realsense: " + ", ".join(f"{k} {v}" for k, v in info.items()))
    if not info.get("usb", "3").startswith("3"):
        print("  WARNING: USB 2 — plug the camera into a USB 3 port (blue) with a USB 3 cable")
    exposure = camera.exposure()
    manual = cfg["camera"].get("exposure") is not None
    print(f"  exposure {exposure['exposure']:.0f}, white balance {exposure['white_balance']:.0f}")
    if not manual:
        print("  (auto; to fix them, copy into config/local.yaml: camera.exposure, white_balance)")
    intrinsics = camera.intrinsics()
    m = intrinsics["camera_matrix"]
    print(
        f"  intrinsics: fx {m[0][0]:.1f}, fy {m[1][1]:.1f}, cx {m[0][2]:.1f}, cy {m[1][2]:.1f}, "
        f"dist {intrinsics['dist_coeffs']} ({intrinsics['model']})"
    )
    if save_intrinsics:
        path = ROOT / cfg["camera"]["intrinsics_file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"source": "realsense factory", "serial": info.get("serial"), **intrinsics}
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        print(f"  saved: {path}")


def check(
    frame: Frame, i: int, detector: BoardDetector, board: Board, cfg: dict, out: Path
) -> np.ndarray:
    """Знайти дошку, надрукувати підсумок, зберегти кадр і накладення."""
    started = time.perf_counter()
    found = detector.find(frame)
    ms = (time.perf_counter() - started) * 1000
    h, w = frame.shape[:2]
    if found is None:
        print(f"frame {i}: {w}x{h}, board NOT found ({ms:.0f} ms)")
    else:
        mask = BoardMask.whole(found.board_size_mm, cfg["vision"]["mask_mm_per_px"])
        seen = [c.id for c in board.clearings.values() if found.sees(c.center, c.radius_mm)]
        print(
            f"frame {i}: {w}x{h}, board found ({ms:.0f} ms): "
            f"inliers {found.inliers} (min {cfg['board']['detector']['min_inliers']}), "
            f"board visible {mask.coverage([found]):.0%}, "
            f"clearings fully visible {len(seen)}/{len(board.clearings)} {sorted(seen)}"
        )
    overlay = draw(frame, found, board)
    cv2.imwrite(str(out / f"frame_{i:03d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(out / f"overlay_{i:03d}.jpg"), overlay, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return overlay


def draw(frame: Frame, found: BoardHomography | None, board: Board) -> np.ndarray:
    """Контур дошки (червоний) і галявини з мапи (жовті, зелені — видно цілком)."""
    vis = frame.copy()
    thick = max(2, frame.shape[1] // 600)
    if found is None:
        cv2.putText(vis, "board not found", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, RED, thick)
        return vis
    bw, bh = found.board_size_mm
    corners = [BoardPoint(0, 0), BoardPoint(bw, 0), BoardPoint(bw, bh), BoardPoint(0, bh)]
    quad = np.array([found.to_frame_px(p) for p in corners], dtype=np.int32)
    cv2.polylines(vis, [quad], True, RED, thick)
    for c in board.clearings.values():
        # коло галявини: 32 точки по колу в мм дошки -> пікселі кадру (з перспективою)
        angles = np.linspace(0, 2 * np.pi, 32, endpoint=False)
        ring = [
            found.to_frame_px(
                BoardPoint(
                    c.center.x + c.radius_mm * np.cos(a), c.center.y + c.radius_mm * np.sin(a)
                )
            )
            for a in angles
        ]
        color = GREEN if found.sees(c.center, c.radius_mm) else YELLOW
        cv2.polylines(vis, [np.array(ring, dtype=np.int32)], True, color, thick)
        x, y = found.to_frame_px(c.center)
        cv2.putText(vis, str(c.id), (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, thick)
    label = f"inliers {found.inliers}"
    cv2.putText(vis, label, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, RED, thick)
    return vis


def live(camera, detector: BoardDetector, board: Board, cfg: dict, out: Path) -> None:
    """Вікно наживо (SIFT ~ кілька кадрів за секунду): s — зберегти кадр, q / Esc — вийти."""
    print("live: s = save frame, q = quit")
    saved = 0
    while True:
        frame = camera.capture()
        found = detector.find(frame)
        vis = draw(frame, found, board)
        scale = 1280 / vis.shape[1]
        cv2.imshow("check_camera", cv2.resize(vis, None, fx=scale, fy=scale))
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            check(frame, saved, detector, board, cfg, out)
            saved += 1
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
