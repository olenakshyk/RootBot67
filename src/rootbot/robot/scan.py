"""Scanner — огляд дошки: рука стає в оглядові пози, на кожній зупинці кадр (ADR 0005).

Зовнішній опис: docs/architecture.md -> robot -> scan;
докладно: docs/robot.md -> «Огляд дошки».

Пози — з бібліотеки camera.scan_poses_file (scripts/explore_scan_poses.py): для кожної відомо,
яку частину дошки з неї видно і які галявини цілком. Рух — за кутами суглобів з бібліотеки,
без оберненої кінематики під час гри. Вибір пози:
- scan(None) — лише початкова поза (перша в бібліотеці);
- "not visible" — жадібно пози, що найбільше перекривають request.uncovered, поки є що додати;
- інші причини ("disagree", "low confidence", "after battles") — пози, з яких вказані галявини
  видно цілком; лише ті, що ще не використовувались у цьому огляді (той самий вид не допоможе).
Жодна поза не допомагає — порожній список кадрів, Orchestrator більше не перепитує.

Рух у просторі суглобів не тримає висоту кінця руки, тому напряму — лише малі переходи
(кожен суглоб не більше camera.direct_step_deg). Більші — через початкову позу: переходи
початкова <-> поза бібліотеки перевірив explore_scan_poses.py. Якщо рука далі від початкової
пози, ніж camera.max_joint_step_deg (наприклад, після place), — ArmError, рука не рухається.
Огляд — від scan(None) або запиту не з переглядів (наприклад, "after battles") до наступного такого.
Скільки разів переглядати — camera.max_rechecks, цикл веде Orchestrator.

Режим debug (logging.level: debug): кожен кадр зберігається в теку поруч із файлом логу
(logs/<час запуску>/scan_<NNN>_frame_<i>.jpg, JPEG 95) разом зі scan_<NNN>.yaml — причина огляду,
галявини запиту, пози, час. Для розбору помилок і даних для тестів.

Без бібліотеки поз рука не рухається: один кадр з поточного місця на кожен огляд.
Заплановано: кадр зблизька перед захватом, перевірка після place.
"""

import logging
import time
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from rootbot.common.errors import FatalError
from rootbot.common.types import ClearingId
from rootbot.config import ROOT
from rootbot.robot.arm import Arm, ArmError
from rootbot.robot.scan_poses import ScanPose, joint_step, load_scan_poses
from rootbot.vision.board_mask import BoardMask
from rootbot.vision.camera import Camera, Frame
from rootbot.vision.observation import RecheckRequest

log = logging.getLogger(__name__)

# причини переглядів у межах одного огляду; решта запитів починає новий огляд
RECHECKS = ("not visible", "disagree", "low confidence")


class Scanner:
    def __init__(
        self,
        arm: Arm,
        camera: Camera,
        poses: list[ScanPose],
        *,
        direct_step_deg: float,
        max_step_deg: float,
        settle_s: float = 0.0,
        frames_dir: Path | None = None,
    ) -> None:
        self.arm = arm
        self.camera = camera
        self.poses = poses
        self.direct_step_deg = direct_step_deg  # менші переходи — напряму, більші — через початкову
        self.max_step_deg = max_step_deg  # далі від початкової пози рука сама не їде
        self.settle_s = settle_s  # пауза після руху перед кадром (вібрації)
        self.frames_dir = frames_dir  # куди зберігати кадри (debug); None — не зберігати
        self._scans = 0  # скільки оглядів зроблено за запуск
        self._used: set[str] = set()  # пози, з яких уже знімали в цьому огляді

    def scan(self, request: RecheckRequest | None = None) -> list[Frame]:
        """Огляд з початкової пози або перегляд того, що просить детектор."""
        if request is None or request.reason not in RECHECKS:
            self._used = set()
        if request is None:
            log.info("scan: board")
        else:
            log.info(f"scan: clearings {request.clearings} ({request.reason})")
        if self.poses:
            poses = self.choose(request)
            frames = [self.shoot(pose) for pose in poses]
            self._used |= {pose.name for pose in poses}
        else:
            poses, frames = [], [self.camera.capture()]
        self._scans += 1
        if self.frames_dir is not None:
            self._save(frames, request, poses)
        return frames

    def choose(self, request: RecheckRequest | None) -> list[ScanPose]:
        """Пози для огляду чи перегляду (рука не рухається)."""
        if request is None:
            poses = [self.poses[0]]
        elif request.reason == "not visible" and request.uncovered is not None:
            poses = self._covering(request.uncovered)
        else:
            poses = self._seeing(request.clearings)
        if not poses:
            log.warning(f"scan: no scan pose helps with {request}, no frames")
        else:
            log.info(f"scan: poses {[p.name for p in poses]}")
        return poses

    def shoot(self, pose: ScanPose, speed_deg_s: float | None = None) -> Frame:
        """Стати в позу, дочекатися, поки рука заспокоїться, і зняти кадр."""
        for joints in self.route(pose.joints):
            self.arm.move_joints(joints, speed_deg_s)
        time.sleep(self.settle_s)
        return self.camera.capture()

    def route(self, target: list[float]) -> list[list[float]]:
        """Кути, через які їхати до target: напряму або через початкову позу (рука не рухається)."""
        here = self.arm.joints()
        if joint_step(here, target) <= self.direct_step_deg:
            return [target]
        start = self.poses[0].joints
        step = joint_step(here, start)
        if step > self.max_step_deg:
            raise ArmError(
                f"the arm is {step:.0f} deg from the scan start pose "
                f"(max {self.max_step_deg}): bring it closer, it will not move there by itself"
            )
        return [start] if target == start else [start, target]

    def _covering(self, uncovered: BoardMask) -> list[ScanPose]:
        """Жадібно ще не використані пози, що додають найбільше непокритого, поки є що додати."""
        left = uncovered
        rest = [p for p in self.poses if p.name not in self._used]
        chosen: list[ScanPose] = []
        while rest and left.area():
            gain = {p.name: (left & left.polygon(p.sees)).area() for p in rest}
            best = max(rest, key=lambda p: gain[p.name])
            if gain[best.name] == 0:
                break
            chosen.append(best)
            left = left - left.polygon(best.sees)
            rest.remove(best)
        return chosen

    def _seeing(self, clearings: list[ClearingId]) -> list[ScanPose]:
        """Нові для огляду пози, з яких галявини видно цілком; спершу ті, що бачать більше."""
        left = set(clearings)
        rest = [p for p in self.poses if p.name not in self._used]
        chosen: list[ScanPose] = []
        while left:
            useful = [p for p in rest if p.clearings & left]
            if not useful:
                log.warning(f"scan: no new scan pose sees clearings {sorted(left)} whole")
                break
            best = max(useful, key=lambda p: len(p.clearings & left))
            chosen.append(best)
            left -= best.clearings
            rest.remove(best)
        return chosen

    def _save(
        self, frames: list[Frame], request: RecheckRequest | None, poses: list[ScanPose]
    ) -> None:
        """Кадри огляду + опис (debug)."""
        assert self.frames_dir is not None
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        name = f"scan_{self._scans:03d}"
        for i, frame in enumerate(frames):
            path = self.frames_dir / f"{name}_frame_{i}.jpg"
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        meta = {
            "reason": "board" if request is None else request.reason,
            "clearings": [] if request is None else [int(c) for c in request.clearings],
            "poses": [p.name for p in poses],
            "frames": len(frames),
            "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        }
        (self.frames_dir / f"{name}.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True))
        log.debug(f"scan: {len(frames)} frames saved to {self.frames_dir}/{name}_*")


def create_scanner(cfg: dict, arm: Arm, camera: Camera, frames_dir: Path | None = None) -> Scanner:
    c = cfg["camera"]
    path = ROOT / c["scan_poses_file"]
    try:
        poses = load_scan_poses(path) if path.exists() else []
    except ValueError as e:
        raise FatalError(f"camera.scan_poses_file: {e}") from e
    if not poses:
        log.warning(
            f"no scan poses in {path} (scripts/explore_scan_poses.py): "
            "every scan is one frame from where the arm stands"
        )
    return Scanner(
        arm,
        camera,
        poses,
        direct_step_deg=c["direct_step_deg"],
        max_step_deg=c["max_joint_step_deg"],
        settle_s=c["settle_s"],
        frames_dir=frames_dir,
    )
