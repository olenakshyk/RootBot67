"""Scanner: вибір оглядових поз за запитом детектора, рух перед кадром, кадри в debug."""

import numpy as np
import pytest
import yaml

from rootbot.common.errors import FatalError
from rootbot.common.types import BoardPoint, ClearingId
from rootbot.robot.arm import ArmError
from rootbot.robot.scan import Scanner, create_scanner
from rootbot.robot.scan_poses import ScanPose
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.observation import RecheckRequest


class FakeCamera:
    def __init__(self, arm=None):
        self.arm = arm
        self.shots = []  # кути руки на момент кожного кадру

    def capture(self):
        self.shots.append(self.arm.moves[-1] if self.arm and self.arm.moves else None)
        return np.full((20, 30, 3), 128, dtype=np.uint8)


class FakeArm:
    def __init__(self, joints=None):
        self.moves = []
        self.start = joints or [0.0] * 7  # де рука стоїть до першого руху

    def home(self):
        pass

    def joints(self):
        return self.moves[-1] if self.moves else self.start

    def move_joints(self, joints, speed_deg_s=None):
        self.moves.append(joints)


def pose(name, n, x0, x1, clearings):
    """Поза з кутами [n]*7, що бачить смугу дошки x0..x1 мм на всю висоту."""
    sees = [BoardPoint(x0, 0), BoardPoint(x1, 0), BoardPoint(x1, 100), BoardPoint(x0, 100)]
    return ScanPose(name, [float(n)] * 7, sees, frozenset(ClearingId(c) for c in clearings))


# дошка 100 x 100 мм: start бачить ліву частину, right — праву, edge — лише край
POSES = [
    pose("start", 0, 0, 60, {1, 2}),
    pose("right", 1, 40, 90, {2, 3}),
    pose("edge", 2, 80, 100, set()),
]


def strip(x0, x1):
    """Смуга дошки x0..x1 мм на всю висоту (маска непокритого)."""
    mask = BoardMask.whole((100, 100), 1).empty()
    mask.grid[:, x0:x1] = True
    return mask


def scanner(poses=POSES, arm=None, **kwargs):
    arm = arm or FakeArm()
    scanner = Scanner(arm, FakeCamera(arm), poses, direct_step_deg=20, max_step_deg=60, **kwargs)
    return scanner, arm


def names(arm):
    return [POSES[int(joints[0])].name for joints in arm.moves]


def test_board_scan_is_one_frame_from_the_start_pose():
    s, arm = scanner()
    frames = s.scan()
    assert len(frames) == 1
    assert names(arm) == ["start"]
    assert s.camera.shots == [POSES[0].joints]  # кадр після руху, а не до


def test_not_visible_greedily_covers_the_uncovered_part():
    s, arm = scanner()
    frames = s.scan(RecheckRequest([ClearingId(3)], "not visible", strip(60, 100)))
    assert names(arm) == ["right", "edge"]  # right додає 30 мм, далі edge — решту 10 мм
    assert len(frames) == 2


def test_not_visible_stops_when_poses_add_nothing():
    s, arm = scanner()
    s.scan(RecheckRequest([ClearingId(3)], "not visible", strip(85, 100)))
    assert names(arm) == ["edge"]  # right додав би 5 мм, але edge уже покрив усе


def test_not_visible_skips_poses_used_in_this_scan():
    s, arm = scanner()
    s.scan()
    s.scan(RecheckRequest([], "not visible", strip(0, 100)))
    assert names(arm) == ["start", "right", "edge"]


def test_disagree_prefers_a_pose_not_used_in_this_scan():
    s, arm = scanner()
    s.scan()
    s.scan(RecheckRequest([ClearingId(2)], "disagree"))
    assert names(arm)[1:] == ["right"]


def test_clearings_need_several_poses_when_one_does_not_see_all():
    s, arm = scanner()
    frames = s.scan(RecheckRequest([ClearingId(1), ClearingId(3)], "low confidence"))
    assert names(arm) == ["start", "right"]
    assert len(frames) == 2


def test_used_pose_is_not_taken_again_the_same_view_does_not_help():
    s, arm = scanner()
    s.scan()
    assert s.scan(RecheckRequest([ClearingId(1)], "low confidence")) == []
    assert names(arm) == ["start"]


def test_other_reasons_start_a_new_scan():
    s, arm = scanner()
    s.scan()
    s.scan(RecheckRequest([ClearingId(2)], "disagree"))
    s.scan(RecheckRequest([ClearingId(2)], "after battles"))  # новий огляд: start знову новий
    s.scan(RecheckRequest([ClearingId(2)], "disagree"))
    assert names(arm) == ["start", "right", "start", "right"]


def test_no_pose_sees_the_clearing_no_move_no_frames():
    s, arm = scanner()
    assert s.scan(RecheckRequest([ClearingId(7)], "disagree")) == []
    assert arm.moves == []


def test_without_pose_library_one_frame_from_where_the_arm_stands():
    s, arm = scanner(poses=[])
    assert len(s.scan()) == 1
    assert len(s.scan(RecheckRequest([ClearingId(5)], "disagree"))) == 1
    assert arm.moves == []


def test_far_poses_are_reached_through_the_start_pose():
    start, left, far = POSES[0], pose("left", 10, 0, 30, {4}), pose("far", 40, 50, 100, {3})
    s, arm = scanner([start, left, far])
    s.scan(RecheckRequest([ClearingId(3)], "after battles"))
    assert arm.moves == [start.joints, far.joints]  # уже в start (рух на місці), далі 40 градусів
    s.scan(RecheckRequest([ClearingId(4)], "disagree"))
    assert arm.moves[2:] == [start.joints, left.joints]  # far -> left 30 градусів: через start
    s.scan()
    assert arm.moves[4:] == [start.joints]  # left -> start 10 градусів: напряму


def test_arm_far_from_the_start_pose_does_not_move():
    s, arm = scanner(arm=FakeArm([100.0] * 7))
    with pytest.raises(ArmError, match="start pose"):
        s.scan()
    assert arm.moves == []


def test_frames_are_saved_in_debug(tmp_path):
    s, _ = scanner(frames_dir=tmp_path)
    s.scan()
    s.scan(RecheckRequest([ClearingId(1), ClearingId(3)], "after battles"))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [
        "scan_001.yaml",
        "scan_001_frame_0.jpg",
        "scan_002.yaml",
        "scan_002_frame_0.jpg",
        "scan_002_frame_1.jpg",
    ]
    meta = yaml.safe_load((tmp_path / "scan_002.yaml").read_text())
    assert (meta["reason"], meta["clearings"], meta["frames"]) == ("after battles", [1, 3], 2)
    assert meta["poses"] == ["start", "right"]


def test_nothing_is_saved_without_frames_dir(tmp_path):
    scanner()[0].scan()
    assert list(tmp_path.iterdir()) == []


CAMERA = {"settle_s": 0.0, "direct_step_deg": 20, "max_joint_step_deg": 60}


def test_create_scanner_reads_the_pose_library(tmp_path):
    path = tmp_path / "scan_poses.yaml"
    library = {
        "poses": [
            {
                "name": "start",
                "joints": [0, 10.1, 0, 129.9, 0, 120, 90],
                "inliers": 947,
                "clearings": [3, 4],
                "sees": [[-157.8, 14.2], [715.7, 11.7], [753.0, 447.9], [-193.9, 450.6]],
            }
        ]
    }
    path.write_text(yaml.safe_dump(library))
    cfg = {"camera": {"scan_poses_file": str(path), **CAMERA}}
    s = create_scanner(cfg, FakeArm(), FakeCamera())
    [start] = s.poses
    assert start.name == "start"
    assert start.joints[3] == 129.9
    assert start.clearings == {3, 4}
    assert start.sees[2] == BoardPoint(753.0, 447.9)


def test_create_scanner_without_library_file(tmp_path):
    cfg = {"camera": {"scan_poses_file": str(tmp_path / "missing.yaml"), **CAMERA}}
    assert create_scanner(cfg, FakeArm(), FakeCamera()).poses == []


def test_broken_pose_library_is_fatal(tmp_path):
    path = tmp_path / "scan_poses.yaml"
    path.write_text(
        yaml.safe_dump({"poses": [{"name": "a", "joints": [0] * 6, "sees": [], "clearings": []}]})
    )
    with pytest.raises(FatalError, match="7 joint angles"):
        create_scanner({"camera": {"scan_poses_file": str(path), **CAMERA}}, FakeArm(), None)
