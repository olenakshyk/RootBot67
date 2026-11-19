"""Honest evaluation of a piece detector on the human-labelled frames of v6.

Step 1 (needs ultralytics): predict every frame once and cache the raw boxes (conf >= 0.05):
    python scripts/detector/evaluate.py predict <weights.pt> <list.txt> <out.json> --imgsz 960 [--tiles]
Step 2 (numpy only): score the cache.
    python scripts/detector/evaluate.py score <preds.json> [--conf 0.5] [--boot 2000]

Metrics (all on human labels only):
- team P / R ("as on the labelling page", v6 meta/metrics.md): a model box is right when its centre lies inside
  the human box (within half its width and height) and the class matches; one human box - one model box;
  confidence >= --conf. P = right / model boxes, R = right / human boxes. Reported per source and in total.
- counts: for every (frame, class) pair, is the number of model boxes exactly the human number;
  plus mean absolute count error per frame and class. This is what the robot needs.
- 95 % ranges: bootstrap over frames (frames are resampled, not boxes, because boxes in one frame are not
  independent). With 41 test frames, differences inside the range are noise.
mAP50 / mAP50-95 come from ultralytics `model.val` (see the notebook / run_eval.sh), not from this file.
"""
import argparse
import json
from pathlib import Path

import numpy as np


def source_of(path):
    # .../v6_2026-11-09/<source>/yolo/images/<part>/<frame>.jpg
    return Path(path).parts[-5]


def load_gt(img_path, W, H):
    lab = Path(str(img_path).replace("/images/", "/labels/")).with_suffix(".txt")
    rows = [r.split() for r in lab.read_text().splitlines() if r.strip()] if lab.exists() else []
    a = np.array(rows, dtype=float).reshape(-1, 5)
    cls = a[:, 0].astype(int)
    cx, cy, w, h = a[:, 1] * W, a[:, 2] * H, a[:, 3] * W, a[:, 4] * H
    return np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1), cls


# ---------------------------------------------------------------- predict (ultralytics)
def predict(args):
    import cv2
    import torch
    from torchvision.ops import batched_nms
    from ultralytics import YOLO

    model = YOLO(args.weights)
    paths = [p for p in Path(args.list).read_text().split() if p]
    out = []
    for p in paths:
        img = cv2.imread(p)
        H, W = img.shape[:2]
        crops = [(0, 0, img)]
        if args.tiles:  # 2x2 tiles with 10 % overlap, as the team's labelling page does
            tw, th = int(W * 0.55), int(H * 0.55)
            for x0 in (0, W - tw):
                for y0 in (0, H - th):
                    crops.append((x0, y0, img[y0:y0 + th, x0:x0 + tw]))
        B, S, C = [], [], []
        for x0, y0, crop in crops:
            r = model.predict(crop, imgsz=args.imgsz, conf=0.05, iou=0.6, max_det=1000, verbose=False)[0]
            b = r.boxes.xyxy.cpu().numpy()
            keep = np.ones(len(b), bool)
            if crop is not img:  # a box touching an inner tile edge is a cut piece: the full frame has it whole
                h, w = crop.shape[:2]
                if x0 > 0:
                    keep &= b[:, 0] > 3
                if x0 + w < W:
                    keep &= b[:, 2] < w - 3
                if y0 > 0:
                    keep &= b[:, 1] > 3
                if y0 + h < H:
                    keep &= b[:, 3] < h - 3
            B.append(b[keep] + np.array([x0, y0, x0, y0]))
            S.append(r.boxes.conf.cpu().numpy()[keep])
            C.append(r.boxes.cls.cpu().numpy().astype(int)[keep])
        b, s, c = np.concatenate(B), np.concatenate(S), np.concatenate(C)
        if len(crops) > 1 and len(b):
            keep = batched_nms(torch.tensor(b, dtype=torch.float32), torch.tensor(s, dtype=torch.float32),
                               torch.tensor(c), 0.5).numpy()
            b, s, c = b[keep], s[keep], c[keep]
        out.append({"path": p, "W": W, "H": H, "boxes": b.round(1).tolist(), "conf": s.round(4).tolist(),
                    "cls": c.tolist()})
        print(f"{len(out)}/{len(paths)} {Path(p).name}: {len(b)} boxes", flush=True)
    Path(args.out).write_text(json.dumps({"weights": str(args.weights), "imgsz": args.imgsz,
                                          "tiles": args.tiles, "frames": out}))


# ---------------------------------------------------------------- score (numpy only)
def match_frame(pb, pc, ps, gb, gc, conf):
    """Return (right, n_pred, n_true, kept classes) with the team's centre-in-box rule, greedy by confidence."""
    k = ps >= conf
    pb, pc, ps = pb[k], pc[k], ps[k]
    order = np.argsort(-ps)
    used = np.zeros(len(gb), bool)
    gcx, gcy = (gb[:, 0] + gb[:, 2]) / 2, (gb[:, 1] + gb[:, 3]) / 2
    gw, gh = gb[:, 2] - gb[:, 0], gb[:, 3] - gb[:, 1]
    right = 0
    for i in order:
        cx, cy = (pb[i, 0] + pb[i, 2]) / 2, (pb[i, 1] + pb[i, 3]) / 2
        ok = (~used) & (gc == pc[i]) & (np.abs(cx - gcx) <= gw / 2) & (np.abs(cy - gcy) <= gh / 2)
        if ok.any():
            j = np.flatnonzero(ok)[np.argmin(np.hypot(cx - gcx[ok], cy - gcy[ok]))]
            used[j] = True
            right += 1
    return right, len(pb), len(gb), pc


def frame_stats(frames, conf, n_cls):
    rows = []
    for f in frames:
        gb, gc = load_gt(f["path"], f["W"], f["H"])
        pb = np.array(f["boxes"], float).reshape(-1, 4)
        pc, ps = np.array(f["cls"], int), np.array(f["conf"], float)
        right, n_pred, n_true, pc_kept = match_frame(pb, pc, ps, gb, gc, conf)
        cp = np.bincount(pc_kept, minlength=n_cls)
        ct = np.bincount(gc, minlength=n_cls)
        present = (cp + ct) > 0  # only classes that are on the frame or predicted count
        rows.append({"source": source_of(f["path"]), "right": right, "pred": n_pred, "true": n_true,
                     "cnt_exact": int(((cp == ct) & present).sum()), "cnt_pairs": int(present.sum()),
                     "cnt_abs_err": int(np.abs(cp - ct)[present].sum())})
    return rows


def summarize(rows):
    r = {k: sum(x[k] for x in rows) for k in ("right", "pred", "true", "cnt_exact", "cnt_pairs", "cnt_abs_err")}
    return {"P": r["right"] / max(r["pred"], 1), "R": r["right"] / max(r["true"], 1),
            "count_exact": r["cnt_exact"] / max(r["cnt_pairs"], 1),
            "count_mae": r["cnt_abs_err"] / max(r["cnt_pairs"], 1),
            "frames": len(rows), "true": r["true"], "pred": r["pred"]}


def bootstrap(rows, n, seed=0):
    rng = np.random.default_rng(seed)
    keys = ("P", "R", "count_exact")
    samples = {k: [] for k in keys}
    for _ in range(n):
        s = summarize([rows[i] for i in rng.integers(0, len(rows), len(rows))])
        for k in keys:
            samples[k].append(s[k])
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in samples.items()}


def score(args):
    d = json.loads(Path(args.preds).read_text())
    n_cls = 12
    rows = frame_stats(d["frames"], args.conf, n_cls)
    res = {"preds": args.preds, "conf": args.conf, "all": summarize(rows), "ci95": bootstrap(rows, args.boot),
           "sources": {s: summarize([r for r in rows if r["source"] == s])
                       for s in sorted({r["source"] for r in rows})}}
    print(json.dumps(res, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1))
    return res


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("predict")
    p.add_argument("weights")
    p.add_argument("list")
    p.add_argument("out")
    p.add_argument("--imgsz", type=int, default=960)
    p.add_argument("--tiles", action="store_true")
    s = sp.add_parser("score")
    s.add_argument("preds")
    s.add_argument("--conf", type=float, default=0.5)
    s.add_argument("--boot", type=int, default=2000)
    s.add_argument("--out")
    a = ap.parse_args()
    predict(a) if a.cmd == "predict" else score(a)


if __name__ == "__main__":
    main()
