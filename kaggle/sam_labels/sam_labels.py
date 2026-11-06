"""Points -> exact piece outlines with SAM 2.1, saved as a YOLO-seg dataset.

Inputs (Kaggle datasets):
  rootbot-frames  frames <stem>.jpg
  rootbot-points  points.json {stem: [{"x", "y", "cls"}]} (full-res px) + piece_classes.txt
Outputs (/kaggle/working):
  dataset/images/*.jpg, dataset/labels/*.txt, dataset/data.yaml   YOLO-seg, ready to train
  previews/*.jpg      outlines drawn on the frame, for a visual check
  report.csv          per frame: points, masks kept, masks that looked wrong
  cvat_import.zip     "Ultralytics YOLO Segmentation 1.0" zip for checking/fixing in CVAT

How each point becomes an outline:
  1. SAM on the whole frame, one prompt per point -> rough mask and piece size
  2. SAM again on a crop around the point (3x piece size) -> sharper edges
  3. keep only the part connected to the point; pixels claimed by two pieces go to the
     piece whose point is nearer
"""

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ultralytics"], check=True)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from ultralytics import SAM  # noqa: E402

IN = Path("/kaggle/input")
OUT = Path("/kaggle/working")
DRAW = [(0, 140, 255), (0, 0, 255), (255, 80, 0), (255, 0, 255), (0, 200, 0),
        (0, 255, 255), (255, 255, 0), (200, 200, 200), (100, 100, 255), (255, 100, 100)]


def find(name):
    hits = list(IN.rglob(name))
    assert hits, f"{name} not found in {IN}"
    return hits[0]


def component_at(mask, x, y):
    """Part of the mask connected to (x, y); empty if the point is outside the mask."""
    n, lab = cv2.connectedComponents(mask.astype(np.uint8))
    if n <= 1 or not (0 <= y < mask.shape[0] and 0 <= x < mask.shape[1]):
        return np.zeros_like(mask, bool)
    i = lab[y, x]
    if i == 0:  # point just outside: take the nearest component
        ys, xs = np.nonzero(lab)
        j = np.argmin((xs - x) ** 2 + (ys - y) ** 2)
        i = lab[ys[j], xs[j]]
    return lab == i


def sam_masks(model, img, pts):
    r = model(img, points=[[p[0], p[1]] for p in pts], labels=[1] * len(pts), verbose=False)[0]
    if r.masks is None:
        return [np.zeros(img.shape[:2], bool) for _ in pts]
    return [m.astype(bool) for m in r.masks.data.cpu().numpy()]


def refine(model, img, x, y, rough):
    ys, xs = np.nonzero(rough)
    size = max(np.ptp(xs), np.ptp(ys)) if len(xs) else 60
    half = int(max(1.5 * size, 128))
    h, w = img.shape[:2]
    x0, y0, x1, y1 = max(0, x - half), max(0, y - half), min(w, x + half), min(h, y + half)
    crop = np.ascontiguousarray(img[y0:y1, x0:x1])
    m = sam_masks(model, crop, [(x - x0, y - y0)])[0]
    m = component_at(m, x - x0, y - y0)
    full = np.zeros(img.shape[:2], bool)
    full[y0:y1, x0:x1] = m
    a_new, a_old = full.sum(), rough.sum()
    if a_old and not (0.3 * a_old <= a_new <= 3 * a_old):
        return rough  # refinement went wrong, keep the rough one
    return full if a_new else rough


def main():
    classes = find("piece_classes.txt").read_text().split()
    points = json.loads(find("points.json").read_text())
    frames = {p.stem: p for p in IN.rglob("*.jpg")}
    ds = OUT / "dataset"
    for d in ["images", "labels"]:
        (ds / d).mkdir(parents=True, exist_ok=True)
    (OUT / "previews").mkdir(exist_ok=True)
    model = SAM("sam2.1_b.pt")
    rows = []
    for stem, pts in sorted(points.items()):
        img = cv2.imread(str(frames[stem]))
        h, w = img.shape[:2]
        xy = [(int(p["x"]), int(p["y"])) for p in pts]
        cls = [classes.index(p["cls"]) for p in pts]
        rough = sam_masks(model, img, xy) if xy else []
        masks = [refine(model, img, x, y, component_at(m, x, y)) for (x, y), m in zip(xy, rough)]
        # overlapping pixels -> nearest point
        if masks:
            stack = np.stack(masks)
            multi = stack.sum(0) > 1
            if multi.any():
                yy, xx = np.nonzero(multi)
                d = np.stack([(xx - x) ** 2 + (yy - y) ** 2 for x, y in xy])
                d[~stack[:, yy, xx]] = np.iinfo(d.dtype).max
                owner = d.argmin(0)
                stack[:, yy, xx] = False
                stack[owner, yy, xx] = True
            masks = list(stack)
        areas = [m.sum() for m in masks]
        med = {c: np.median([a for a, k in zip(areas, cls) if k == c and a]) for c in set(cls)}
        lines, bad = [], 0
        over = img.copy()
        for m, c, a, (x, y) in zip(masks, cls, areas, xy):
            cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            odd = not cnts or not a or not (0.25 * med[c] <= a <= 4 * med[c])
            bad += odd
            if not cnts:
                cv2.drawMarker(over, (x, y), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 30, 4)
                continue
            poly = cv2.approxPolyDP(max(cnts, key=cv2.contourArea), 1.0, True).reshape(-1, 2)
            if len(poly) >= 3:
                lines.append(f"{c} " + " ".join(f"{px / w:.5f} {py / h:.5f}" for px, py in poly))
            col = DRAW[c % len(DRAW)]
            cv2.fillPoly(over, [poly], col)
            cv2.polylines(img, [poly], True, (0, 0, 255) if odd else col, 2)
        (ds / "labels" / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        shutil.copy(frames[stem], ds / "images" / f"{stem}.jpg")
        prev = cv2.addWeighted(img, 0.6, over, 0.4, 0)
        cv2.imwrite(str(OUT / "previews" / f"{stem}.jpg"),
                    cv2.resize(prev, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
        rows.append([stem, len(pts), len(lines), bad])
        print(stem, len(pts), len(lines), bad, flush=True)
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(classes))
    (ds / "data.yaml").write_text(f"path: .\ntrain: images\nval: images\nnames:\n{names}\n")
    with open(OUT / "report.csv", "w", newline="") as f:
        csv.writer(f).writerows([["frame", "points", "masks", "odd_masks"], *rows])
    # CVAT "Ultralytics YOLO Segmentation 1.0" import layout
    cv = OUT / "cvat"
    (cv / "labels" / "train").mkdir(parents=True, exist_ok=True)
    for p in (ds / "labels").glob("*.txt"):
        shutil.copy(p, cv / "labels" / "train" / p.name)
    (cv / "data.yaml").write_text(f"names:\n{names}\npath: .\ntrain: train.txt\n")
    (cv / "train.txt").write_text("".join(f"images/train/{s}.jpg\n" for s, *_ in rows))
    shutil.make_archive(str(OUT / "cvat_import"), "zip", cv)
    shutil.rmtree(cv)
    print("done", len(rows), "frames")


main()
