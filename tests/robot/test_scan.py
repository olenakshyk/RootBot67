"""Scanner: у debug кожен кадр огляду зберігається з описом."""

import numpy as np
import yaml

from rootbot.common.types import ClearingId
from rootbot.robot.scan import Scanner
from rootbot.vision.observation import RecheckRequest


class FakeCamera:
    def capture(self):
        return np.full((20, 30, 3), 128, dtype=np.uint8)


class FakeArm:
    def home(self):
        pass


def test_frames_are_saved_in_debug(tmp_path):
    scanner = Scanner(FakeArm(), FakeCamera(), scan_poses=[[0], [1]], frames_dir=tmp_path)
    scanner.scan()
    scanner.scan(RecheckRequest([ClearingId(5)], "disagree"))
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "scan_001.yaml",
        "scan_001_frame_0.jpg",
        "scan_001_frame_1.jpg",
        "scan_002.yaml",
        "scan_002_frame_0.jpg",
    ]
    meta = yaml.safe_load((tmp_path / "scan_002.yaml").read_text())
    assert (meta["reason"], meta["clearings"], meta["frames"]) == ("disagree", [5], 1)


def test_nothing_is_saved_without_frames_dir(tmp_path):
    Scanner(FakeArm(), FakeCamera(), scan_poses=[[0]]).scan()
    assert list(tmp_path.iterdir()) == []
