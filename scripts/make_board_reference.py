r"""Еталон дошки (або планшета бота): знімок -> вид строго зверху з відомим масштабом у мм.

1. Вирівняти знімок. Фото: клікнути 4 кути дошки (або передати їх у --corners), і знімок
   розтягується в прямокутник за розмірами в мм. Скан, уже обрізаний по краях: --aligned.
2. Привести до масштабу board.warp_px_per_mm, зберегти <name>.png + <name>.yaml.
3. Перевірити: <name>_check.png — кола галявин з карти поверх еталона; і пошук еталона на
   кадрах з --frames (за замовчуванням camera.folder) -> <name>_frames/.

    python scripts/make_board_reference.py --image <фото> --name board_autumn
    python scripts/make_board_reference.py --image <фото> \
        --corners 412 388 3610 352 3702 2810 330 2875
    python scripts/make_board_reference.py --image <скан> --name bot_board \
        --size-mm 280 210 --aligned

Кути — по порядку: (0, 0) лівий верхній -> правий верхній (біля лого ROOT) -> правий нижній ->
лівий нижній; доріжка рахунку — між двома останніми. Порядок кутів задає орієнтацію еталона
(як центри галявин у config/maps/autumn.yaml), тож саме фото може бути повернуте як завгодно.
З --aligned орієнтацію задає сам знімок: лого ROOT має бути праворуч угорі. Повернутий на 180°
еталон дошку знайде, але галявини мовчки з'їдуть у дзеркальні місця — це й ловить
<name>_check.png: кола мають лягти на галявини.

Див. docs/vision.md -> «V1: дошка й планшет бота».
"""

import argparse
from pathlib import Path

import cv2
import numpy as np
import yaml

from rootbot.config import ROOT, load_config
from rootbot.vision.board_detector import BoardDetector
from rootbot.vision.board_reference import Corners, to_scale, warp_to_board
from rootbot.vision.camera import FolderCamera

RED = (0, 0, 255)
CORNER_NAMES = ["top-left (0, 0)", "top-right (ROOT logo)", "bottom-right", "bottom-left"]


def click_corners(img: np.ndarray) -> Corners:
    """Вікно з фото: ЛКМ — наступний кут, Backspace — скасувати останній, Enter — готово."""
    scale = min(1.0, 1400 / max(img.shape[:2]))  # велике фото зменшуємо, щоб влізло в екран
    view = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    corners: Corners = []

    def on_mouse(event: int, x: int, y: int, *_: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
            corners.append((x / scale, y / scale))  # у пікселях оригінального фото

    win = "board corners: click 4, Enter = done, Backspace = undo, Esc = quit"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    print("click the board corners in order: " + " -> ".join(CORNER_NAMES))
    while True:
        shown = view.copy()
        pts = [(round(x * scale), round(y * scale)) for x, y in corners]
        if len(pts) > 1:
            cv2.polylines(shown, [np.int32(pts)], len(pts) == 4, RED, 2)
        for i, p in enumerate(pts):
            cv2.circle(shown, p, 6, RED, -1)
            cv2.putText(
                shown, str(i + 1), (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, RED, 2
            )
        if len(pts) < 4:
            hint = f"next: {CORNER_NAMES[len(pts)]}"
            cv2.putText(shown, hint, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, RED, 2)
        cv2.imshow(win, shown)
        key = cv2.waitKey(30) & 0xFF
        if key in (10, 13) and len(corners) == 4:
            break
        if key == 8 and corners:
            corners.pop()
        if key == 27:
            raise SystemExit("cancelled")
    cv2.destroyWindow(win)
    return corners


def check_clearings(ref: np.ndarray, map_file: Path, px_per_mm: float) -> np.ndarray:
    """Кола галявин з карти поверх еталона: якщо не лягли на галявини, еталон повернутий."""
    check = ref.copy()
    for c in yaml.safe_load(map_file.read_text(encoding="utf-8"))["clearings"]:
        x, y = (round(v * px_per_mm) for v in c["center_mm"])
        cv2.circle(check, (x, y), round(c["radius_mm"] * px_per_mm), RED, 2)
        cv2.putText(check, str(c["id"]), (x - 8, y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, RED, 2)
    return check


def check_on_frames(
    ref: np.ndarray, px_per_mm: float, params: dict, folder: Path, out: Path
) -> None:
    """Шукає еталон на кожному кадрі з теки, як це робитиме бот, і зберігає накладення
    еталона на кадр: якщо на <out>/<кадр>.png еталон не збігся з дошкою — еталон поганий
    (світло, бліки, різкість) або кадр не той."""
    detector = BoardDetector(
        ref,
        px_per_mm,
        ratio_test=params["ratio_test"],
        ransac_reproj_px=params["ransac_reproj_px"],
        min_inliers=params["min_inliers"],
        min_homography_det=params["min_homography_det"],
    )
    camera = FolderCamera(folder)
    out.mkdir(parents=True, exist_ok=True)
    h, w = ref.shape[:2]
    found_count = 0
    for path in camera.frames:
        frame = camera.capture()
        found = detector.find(frame)
        if found is None:
            print(f"  {path.name}: board NOT found")
            continue
        found_count += 1
        # піксель еталона -> мм дошки -> піксель кадру
        ref_to_frame = np.linalg.inv(found.to_board) @ np.diag([1 / px_per_mm, 1 / px_per_mm, 1.0])
        size = (frame.shape[1], frame.shape[0])
        warped = cv2.warpPerspective(ref, ref_to_frame, size)
        mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), ref_to_frame, size) > 0
        overlay = frame.copy()
        overlay[mask] = cv2.addWeighted(frame, 0.5, warped, 0.5, 0)[mask]
        outline = cv2.perspectiveTransform(
            np.float32([[(0, 0), (w, 0), (w, h), (0, h)]]), ref_to_frame
        )
        cv2.polylines(overlay, [np.int32(outline)], True, RED, 3)
        cv2.imwrite(str(out / f"{path.stem}.png"), overlay)
        print(f"  {path.name}: found")
    print(f"found on {found_count}/{len(camera.frames)} frames, overlays: {out}")


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--image", required=True, help="photo or scan of the board, from above")
    ap.add_argument(
        "--name", default="board_autumn", help="reference name: board_autumn, bot_board"
    )
    ap.add_argument(
        "--size-mm",
        type=float,
        nargs=2,
        default=cfg["board"]["size_mm"],
        metavar=("W", "H"),
        help="outer size in mm (measure with a ruler)",
    )
    ap.add_argument("--px-per-mm", type=float, default=cfg["board"]["warp_px_per_mm"])
    ap.add_argument(
        "--corners",
        type=float,
        nargs=8,
        metavar="XY",
        help="4 board corners on the image, px: x y of top-left, top-right, bottom-right, "
        "bottom-left (instead of clicking)",
    )
    ap.add_argument(
        "--aligned", action="store_true", help="image is already top-down and cropped (a scan)"
    )
    ap.add_argument(
        "--frames",
        type=Path,
        default=ROOT / cfg["camera"]["folder"],
        help="folder with camera frames to check the reference on",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "reference")
    a = ap.parse_args()

    # 1. Завантажити: OpenCV читає зображення як масив numpy
    #    висота x ширина x 3 (кольори в порядку B, G, R, не R, G, B)
    img = cv2.imread(a.image)
    if img is None:
        raise SystemExit(f"cannot read {a.image}")
    h, w = img.shape[:2]
    print(f"image: {w}x{h} px, dtype {img.dtype}, channels {img.shape[2]}")

    # 2. Вирівняти й привести до масштабу: 1 мм дошки = px_per_mm пікселів
    corners: Corners | None = None
    if a.aligned:
        # to_scale розтягне будь-який знімок до size_mm: повернутий на 90° (вертикальний скан
        # горизонтальної дошки) мовчки спотвориться, тож зупиняємось
        if (w > h) != (a.size_mm[0] > a.size_mm[1]):
            raise SystemExit(
                f"image is {w}x{h} px but the board is {a.size_mm[0]:.0f}x{a.size_mm[1]:.0f} mm: "
                "rotate the image by 90 deg (board: ROOT logo top-right, score track at the bottom)"
            )
        ref = to_scale(img, a.size_mm, a.px_per_mm)
    else:
        if a.corners:
            corners = [(a.corners[i], a.corners[i + 1]) for i in range(0, 8, 2)]
        else:
            corners = click_corners(img)
        ref = warp_to_board(img, corners, a.size_mm, a.px_per_mm)
    w_mm, h_mm = a.size_mm
    h_px, w_px = ref.shape[:2]
    print(f"reference: {w_px}x{h_px} px = {w_mm:.0f}x{h_mm:.0f} mm, {a.px_per_mm} px/mm")

    # 3. Зберегти зображення й опис
    a.out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.out / f"{a.name}.png"), ref)
    meta = dict(
        name=a.name,
        source=str(a.image),
        size_mm=[float(v) for v in a.size_mm],
        px_per_mm=a.px_per_mm,
        image=f"{a.name}.png",
        image_size_px=[ref.shape[1], ref.shape[0]],
        corners_px=[[round(x, 1), round(y, 1)] for x, y in corners] if corners else None,
    )
    (a.out / f"{a.name}.yaml").write_text(
        yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"saved: {a.out / (a.name + '.png')} and {a.name}.yaml")

    # 4. Перевірка орієнтації: кола галявин з карти поверх еталона мають лягти на галявини.
    #    Якщо ні — еталон повернутий (SIFT цього не помітить, галявини з'їдуть мовчки).
    if a.name.startswith("board_"):
        check = check_clearings(ref, ROOT / cfg["board"]["map_file"], a.px_per_mm)
        cv2.imwrite(str(a.out / f"{a.name}_check.png"), check)
        print(
            f"check orientation: {a.out / (a.name + '_check.png')} - circles must sit on clearings"
        )

    # 5. Самоперевірка: чи знаходиться еталон на кадрах камери
    if a.frames.is_dir() and FolderCamera(a.frames).frames:
        print(f"looking for the reference on frames from {a.frames}:")
        check_on_frames(
            ref, a.px_per_mm, cfg["board"]["detector"], a.frames, a.out / f"{a.name}_frames"
        )
    else:
        print(f"no frames in {a.frames}: reference not checked on camera frames (use --frames)")


if __name__ == "__main__":
    main()
