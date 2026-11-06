"""Auto-label v0: draft instance masks of Root pieces from colour + shape.

No ML. Wooden pieces are strongly saturated (orange / blue / green), the printed
board is not, so colour thresholds find the pieces. Shape then decides the class:
  - solid blob                      -> warrior
  - blob with white art inside      -> flat piece: round -> token, square -> building
  - blob much larger than a warrior -> split into k warriors (k-means on pixel coords)

Output (YOLO-seg format, ready for a labelling tool where people fix mistakes):
  <out>/labels/<frame>.txt   class x1 y1 x2 y2 ... (normalised polygon)
  <out>/previews/<frame>.jpg overlay for a quick visual check
  <out>/classes.txt, <out>/stats.csv

Runs on Kaggle (input dataset mounted under /kaggle/input) or locally:
  python autolabel.py <frames_dir> <out_dir>
"""

import csv
import sys
from pathlib import Path

import cv2
import numpy as np

CLASSES = ["cat_warrior", "keep", "enemy_warrior", "enemy_building", "enemy_token"]

# HSV ranges in OpenCV units (H 0-179, S/V 0-255). Tuned on 2026-11-05 phone videos.
COLOURS = {
    "orange": [((3, 140, 110), (22, 255, 255))],
    "blue": [((95, 140, 70), (125, 255, 255))],
    "green": [((30, 70, 70), (85, 255, 255))],
}

REF_WIDTH = 2560  # areas below are for a frame this wide, scaled for other sizes
MIN_AREA = 900  # smaller blobs are noise or painted details
MAX_AREA = 60000  # larger blobs are faction boards, cards, etc.
SOLID_FILL = 0.82  # colour pixels / convex hull area above this -> solid wooden piece
WHITE_FRAC = 0.08  # share of white-ish pixels inside the hull above this -> flat piece with art
FLAT_SIZE = (0.4, 3.0)  # flat piece hull area relative to a typical warrior
DEFAULT_WARRIOR = 3600  # typical warrior area (px at REF_WIDTH) when a frame has no clear one
ROUND_CIRC = 0.80  # hull circularity above this -> round token
SPLIT_RATIO = 1.6  # blob area / typical warrior area above this -> several warriors
COLOURS_BGR = [(0, 140, 255), (0, 0, 255), (255, 80, 0), (255, 0, 255), (0, 220, 0)]


def colour_masks(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    out = {}
    for name, ranges in COLOURS.items():
        m = np.zeros(img.shape[:2], np.uint8)
        for lo, hi in ranges:
            m |= cv2.inRange(hsv, np.array(lo), np.array(hi))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
        out[name] = m
    return out


def shape_features(blob):
    cnts, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cnts, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    hull_area = max(cv2.contourArea(hull), 1.0)
    perim = max(cv2.arcLength(hull, True), 1.0)
    fill = float(blob.sum() / 255) / hull_area
    circ = 4 * np.pi * hull_area / perim**2
    return c, hull, hull_area, fill, circ


def split_blob(blob, k):
    ys, xs = np.nonzero(blob)
    pts = np.column_stack([xs, ys]).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, lab, _ = cv2.kmeans(pts, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    parts = []
    for i in range(k):
        m = np.zeros_like(blob)
        sel = lab.ravel() == i
        m[ys[sel], xs[sel]] = 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        parts.append(m)
    return parts


def white_fraction(white, blob, hull):
    """Share of white-ish pixels inside the hull but outside the coloured blob."""
    hm = np.zeros_like(blob)
    cv2.drawContours(hm, [hull], -1, 255, -1)
    inside = hm > 0
    return float((white[inside] & (blob[inside] == 0)).sum()) / max(inside.sum(), 1)


def label_frame(img):
    """Return list of (class_id, contour) for one BGR frame."""
    h, w = img.shape[:2]
    s = (w / REF_WIDTH) ** 2
    min_a, max_a = MIN_AREA * s, MAX_AREA * s
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = (hsv[..., 1] < 60) & (hsv[..., 2] > 170)
    found = []
    for colour, mask in colour_masks(img).items():
        n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        solids, others = [], []
        for i in range(1, n):
            area = st[i, cv2.CC_STAT_AREA]
            if not (min_a <= area <= max_a):
                continue
            blob = np.where(lab == i, 255, 0).astype(np.uint8)
            c, hull, hull_area, fill, circ = shape_features(blob)
            if fill >= SOLID_FILL:
                solids.append((blob, area))
            else:
                others.append((blob, area, hull, hull_area, circ))
        # typical single warrior = median of the smaller half of solid blobs
        areas = sorted(a for _, a in solids)
        typical = np.median(areas[: max(1, (len(areas) + 1) // 2)]) if areas else None
        typical = typical or DEFAULT_WARRIOR * s
        warrior_cls = 0 if colour == "orange" else 2
        flats = []
        for blob, area, hull, hull_area, circ in others:
            wf = white_fraction(white, blob, hull)
            rel = hull_area / typical
            if wf >= WHITE_FRAC and FLAT_SIZE[0] <= rel <= FLAT_SIZE[1]:
                flats.append((hull, circ))  # token / building with white art
            elif wf < WHITE_FRAC and area <= 8 * typical:
                solids.append((blob, area))  # cluster of touching warriors, split below
            # else: big printed areas (player boards, cards) -> ignore
        for blob, area in solids:
            k = int(round(area / typical)) if area > SPLIT_RATIO * typical else 1
            parts = split_blob(blob, min(k, 8)) if k > 1 else [blob]
            for p in parts:
                cnts, _ = cv2.findContours(p, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if cnts:
                    found.append((warrior_cls, max(cnts, key=cv2.contourArea)))
        for hull, circ in flats:
            if colour == "orange":
                cls = 1  # keep candidate, people confirm
            else:
                cls = 4 if circ >= ROUND_CIRC else 3
            found.append((cls, hull))
    return found


def write_yolo(path, found, w, h):
    lines = []
    for cls, c in found:
        c = cv2.approxPolyDP(c, 1.5, True).reshape(-1, 2)
        if len(c) < 3:
            continue
        xy = " ".join(f"{x / w:.5f} {y / h:.5f}" for x, y in c)
        lines.append(f"{cls} {xy}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def preview(img, found):
    over = img.copy()
    for cls, c in found:
        cv2.drawContours(over, [c], -1, COLOURS_BGR[cls], -1)
    out = cv2.addWeighted(img, 0.55, over, 0.45, 0)
    for cls, c in found:
        cv2.drawContours(out, [c], -1, COLOURS_BGR[cls], 2)
    for i, name in enumerate(CLASSES):
        cv2.putText(out, name, (20, 40 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                    COLOURS_BGR[i], 3)
    return cv2.resize(out, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)


def main(src, out):
    src, out = Path(src), Path(out)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    (out / "previews").mkdir(parents=True, exist_ok=True)
    (out / "classes.txt").write_text("\n".join(CLASSES) + "\n")
    frames = sorted(p for p in src.rglob("*.jpg"))
    print(f"{len(frames)} frames in {src}")
    with open(out / "stats.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", *CLASSES])
        for p in frames:
            img = cv2.imread(str(p))
            found = label_frame(img)
            write_yolo(out / "labels" / f"{p.stem}.txt", found, img.shape[1], img.shape[0])
            cv2.imwrite(str(out / "previews" / f"{p.stem}.jpg"), preview(img, found),
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
            counts = [sum(1 for c, _ in found if c == i) for i in range(len(CLASSES))]
            wr.writerow([p.stem, *counts])
    print("done")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:  # Kaggle
        main("/kaggle/input", "/kaggle/working")
