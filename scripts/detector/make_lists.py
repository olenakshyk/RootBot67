"""Build YOLO train/val/test lists from the team dataset v6 (one config for all sources).

Train: every v6 train frame; frames labelled by a human are listed 3 times (same weight the team uses).
Val / test: only human-labelled frames (as in the dataset standard). Also one test list per source.

Usage: python scripts/detector/make_lists.py <v6 dir> <out dir> [--human-weight 3]
"""
import argparse
import json
from pathlib import Path

import yaml

SOURCES = ["bgg", "video_A", "video_B", "scan_s1", "scan_s2", "video_neg", "phone"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("v6", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--human-weight", type=int, default=3)
    a = ap.parse_args()
    v6, out = a.v6.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    lists = {"train": [], "val": [], "test": []}
    per_source_test = {}
    for src in SOURCES:
        man = json.loads((v6 / src / "manifest.json").read_text())
        for fr in man["frames"]:
            part = fr["part"]
            img = v6 / src / "yolo" / "images" / part / f"{fr['frame']}.jpg"
            assert img.exists(), img
            n = a.human_weight if (part == "train" and fr["labelled_by"] == "human") else 1
            lists[part] += [str(img)] * n
            if part == "test":
                per_source_test.setdefault(src, []).append(str(img))
    for part, paths in lists.items():
        (out / f"{part}.txt").write_text("\n".join(paths) + "\n")
    for src, paths in per_source_test.items():
        (out / f"test_{src}.txt").write_text("\n".join(paths) + "\n")
    names = {c["id"]: c["name"] for c in yaml.safe_load((v6 / "classes.yaml").read_text())["classes"]}
    (out / "data.yaml").write_text(yaml.safe_dump(
        {"path": str(out), "train": "train.txt", "val": "val.txt", "test": "test.txt", "names": names},
        sort_keys=False))
    print({k: len(v) for k, v in lists.items()}, {k: len(v) for k, v in per_source_test.items()})


if __name__ == "__main__":
    main()
