"""Еталон дошки (або планшета бота): знімок зверху -> вирівняне зображення з відомим масштабом у мм.

Крок 1 (зараз): завантажити знімок, привести до масштабу px/мм, зберегти + опис.
Далі додамо: вирівнювання за 4 кутами, характерні точки (SIFT),
перевірку на кадрах.

    python scripts/make_board_reference.py --image <знімок> --name board_autumn
    python scripts/make_board_reference.py --image <скан> --name bot_board --size-mm 280 210

Див. docs/vision.md -> «V1: дошка й планшет бота».
"""

import argparse
from pathlib import Path

import cv2
import yaml

from rootbot.config import ROOT, load_config
from rootbot.vision.board_reference import to_scale


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--image", required=True, help="top-down image of the board (scan or photo)")
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
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "reference")
    a = ap.parse_args()

    # 1. Завантажити: OpenCV читає зображення як масив numpy
    #    висота x ширина x 3 (кольори в порядку B, G, R, не R, G, B)
    img = cv2.imread(a.image)
    if img is None:
        raise SystemExit(f"cannot read {a.image}")
    h, w = img.shape[:2]
    print(f"image: {w}x{h} px, dtype {img.dtype}, channels {img.shape[2]}")

    # 2. Масштаб: зробити так, щоб 1 мм дошки = px_per_mm пікселів
    ref = to_scale(img, a.size_mm, a.px_per_mm)
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
    )
    yaml.safe_dump(meta, open(a.out / f"{a.name}.yaml", "w"), allow_unicode=True, sort_keys=False)
    print(f"saved: {a.out / (a.name + '.png')} and {a.name}.yaml")


if __name__ == "__main__":
    main()
