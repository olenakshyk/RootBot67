"""Candidate pieces from colour + shape, for review before SAM.

For each selected frame it writes:
  candidates.json  {frame: [{"id", "x", "y", "cls"}]}  (x, y in full-resolution pixels)
  previews/<frame>.jpg  half-size frame with numbered candidates and a coordinate grid
The candidates are only a starting point: a reviewer fixes classes, removes false ones
and adds missed pieces, then SAM turns each point into an exact outline.

Runs on Kaggle (frames dataset under /kaggle/input) or locally:
  python candidates.py <frames_dir> <out_dir>
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

CLASSES = ["cat_warrior", "keep", "eyrie_warrior", "eyrie_roost",
           "alliance_warrior", "alliance_base", "alliance_sympathy"]
# colour -> (class if solid wooden piece, class if flat square, class if flat round)
COLOUR_CLASSES = {"orange": (0, 1, 1), "blue": (2, 3, 3), "green": (4, 5, 6)}
DRAW = [(0, 140, 255), (0, 0, 255), (255, 80, 0), (255, 0, 255), (0, 200, 0),
        (0, 255, 255), (255, 255, 0)]

SELECTED = """
VID_20261105_200652_0002 VID_20261105_200652_0004 VID_20261105_200652_0006 VID_20261105_200652_0008
VID_20261105_200652_0010 VID_20261105_200652_0016 VID_20261105_200652_0019 VID_20261105_200652_0021
VID_20261105_200652_0023 VID_20261105_200652_0025 VID_20261105_200652_0027 VID_20261105_200652_0029
VID_20261105_200652_0031 VID_20261105_200652_0034 VID_20261105_200652_0037 VID_20261105_200652_0039
VID_20261105_200652_0041 VID_20261105_200652_0043 VID_20261105_200652_0045 VID_20261105_200652_0048
VID_20261105_200652_0050 VID_20261105_200652_0052 VID_20261105_200652_0054 VID_20261105_200652_0056
VID_20261105_200652_0058 VID_20261105_200652_0064 VID_20261105_200652_0066 VID_20261105_200652_0068
VID_20261105_200652_0071 VID_20261105_200652_0074 VID_20261105_200652_0077 VID_20261105_200652_0080
VID_20261105_200652_0083 VID_20261105_200652_0093 VID_20261105_200652_0096 VID_20261105_200652_0098
VID_20261105_200652_0100 VID_20261105_200652_0102 VID_20261105_200652_0122 VID_20261105_200652_0124
VID_20261105_200652_0131 VID_20261105_200652_0134 VID_20261105_200652_0136 VID_20261105_200652_0138
VID_20261105_200652_0139 VID_20261105_200652_0140 VID_20261105_200652_0143 VID_20261105_200652_0146
VID_20261105_200652_0147 VID_20261105_200652_0150 VID_20261105_200652_0151 VID_20261105_200652_0155
VID_20261105_200652_0157 VID_20261105_200652_0159 VID_20261105_200652_0161 VID_20261105_200652_0166
VID_20261105_200652_0169 VID_20261105_201500_0008 VID_20261105_201500_0014 VID_20261105_201500_0019
""".split()

COLOURS = {  # HSV, OpenCV units
    "orange": ((3, 140, 110), (22, 255, 255)),
    "blue": ((95, 140, 70), (125, 255, 255)),
    "green": ((30, 70, 70), (85, 255, 255)),
}
REF_WIDTH = 2560
MIN_AREA, MAX_AREA = 500, 60000
SOLID_FILL, WHITE_FRAC, ROUND_CIRC = 0.82, 0.08, 0.80
FLAT_SIZE, SPLIT_RATIO, DEFAULT_WARRIOR = (0.4, 3.0), 1.6, 3600


def find_candidates(img):
    h, w = img.shape[:2]
    s = (w / REF_WIDTH) ** 2
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = (hsv[..., 1] < 60) & (hsv[..., 2] > 170)
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    out = []
    for colour, (lo, hi) in COLOURS.items():
        solid_cls, square_cls, round_cls = COLOUR_CLASSES[colour]
        m = cv2.inRange(hsv, np.array(lo), np.array(hi))
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k5), cv2.MORPH_CLOSE, k5,
                            iterations=2)
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        blobs = []
        for i in range(1, n):
            area = st[i, cv2.CC_STAT_AREA]
            if not (MIN_AREA * s <= area <= MAX_AREA * s):
                continue
            blob = (lab == i).astype(np.uint8) * 255
            cnts, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
            ha = max(cv2.contourArea(hull), 1.0)
            circ = 4 * np.pi * ha / max(cv2.arcLength(hull, True), 1.0) ** 2
            hm = np.zeros_like(blob)
            cv2.drawContours(hm, [hull], -1, 255, -1)
            inside = hm > 0
            wf = float((white[inside] & (blob[inside] == 0)).sum()) / max(inside.sum(), 1)
            blobs.append((blob, area, area / ha, ha, circ, wf))
        solid_areas = sorted(a for _, a, fill, *_ in blobs if fill >= SOLID_FILL)
        typical = (np.median(solid_areas[: max(1, (len(solid_areas) + 1) // 2)])
                   if solid_areas else DEFAULT_WARRIOR * s)
        for blob, area, fill, ha, circ, wf in blobs:
            if fill < SOLID_FILL and wf >= WHITE_FRAC and FLAT_SIZE[0] <= ha / typical <= FLAT_SIZE[1]:
                ys, xs = np.nonzero(blob)
                out.append((int(xs.mean()), int(ys.mean()),
                            round_cls if circ >= ROUND_CIRC else square_cls))
                continue
            if fill < SOLID_FILL and (wf >= WHITE_FRAC or area > 8 * typical):
                continue  # printed areas, boards, cards
            k = int(round(area / typical)) if area > SPLIT_RATIO * typical else 1
            ys, xs = np.nonzero(blob)
            if k <= 1:
                # point inside the blob farthest from its edge
                d = cv2.distanceTransform(blob, cv2.DIST_L2, 5)
                y, x = np.unravel_index(d.argmax(), d.shape)
                out.append((int(x), int(y), solid_cls))
                continue
            pts = np.column_stack([xs, ys]).astype(np.float32)
            crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
            _, _, centres = cv2.kmeans(pts, min(k, 8), None, crit, 3, cv2.KMEANS_PP_CENTERS)
            out.extend((int(cx), int(cy), solid_cls) for cx, cy in centres)
    return [{"id": i, "x": x, "y": y, "cls": CLASSES[c]} for i, (x, y, c) in enumerate(out)]


def preview(img, cands):
    small = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    for gx in range(0, w, 100):  # grid every 200 px of the full frame
        cv2.line(small, (gx, 0), (gx, h), (255, 255, 255), 1)
        cv2.putText(small, str(gx * 2), (gx + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                    (255, 255, 255), 1)
    for gy in range(0, h, 100):
        cv2.line(small, (0, gy), (w, gy), (255, 255, 255), 1)
        cv2.putText(small, str(gy * 2), (2, gy + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                    (255, 255, 255), 1)
    for c in cands:
        x, y, col = c["x"] // 2, c["y"] // 2, DRAW[CLASSES.index(c["cls"])]
        cv2.circle(small, (x, y), 4, col, -1)
        cv2.circle(small, (x, y), 4, (0, 0, 0), 1)
        cv2.putText(small, str(c["id"]), (x + 5, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (0, 0, 0), 3)
        cv2.putText(small, str(c["id"]), (x + 5, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    return small


def main(src, out):
    src, out = Path(src), Path(out)
    (out / "previews").mkdir(parents=True, exist_ok=True)
    frames = {p.stem: p for p in src.rglob("*.jpg") if p.stem in SELECTED}
    print(f"{len(frames)} of {len(SELECTED)} selected frames found")
    result = {}
    for stem in sorted(frames):
        img = cv2.imread(str(frames[stem]))
        cands = find_candidates(img)
        result[stem] = cands
        cv2.imwrite(str(out / "previews" / f"{stem}.jpg"), preview(img, cands),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    (out / "candidates.json").write_text(json.dumps(result, indent=0))
    print("candidates:", sum(len(v) for v in result.values()))


if __name__ == "__main__":
    if len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:
        main("/kaggle/input", "/kaggle/working")
