"""Scanner — огляд дошки: рука стає в оглядові пози, на кожній зупинці кадр (ADR 0005).

Зовнішній опис: docs/architecture.md -> robot -> scan;
докладно: docs/robot.md -> «Огляд і уточнення камерою».

Спершу огляд з поз config camera.scan_poses (майже згори). Якщо детектор просить переглянути
(RecheckRequest), Scanner сам вирішує як: поза над вказаними галявинами, ближче чи під іншим
кутом залежно від причини. Скільки разів — camera.max_rechecks, цикл веде Orchestrator.

Режим debug (logging.level: debug): кожен кадр зберігається в теку поруч із файлом логу
(logs/<час запуску>/scan_<NNN>_frame_<i>.jpg, JPEG 95) разом зі scan_<NNN>.yaml — причина огляду,
галявини запиту, час. Для розбору помилок і даних для тестів.

Поки поз немає (TBD) і рука не рухається: один кадр з поточного місця на кожен огляд.
Заплановано: поза камери до кожного кадру (поза фланця x hand-eye), кадр зблизька перед захватом,
перевірка після place.
"""

import logging
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from rootbot.robot.arm import Arm
from rootbot.vision.camera import Camera, Frame
from rootbot.vision.observation import RecheckRequest

log = logging.getLogger(__name__)


class Scanner:
    def __init__(
        self,
        arm: Arm,
        camera: Camera,
        scan_poses: list[list[float]],
        frames_dir: Path | None = None,
    ) -> None:
        self.arm = arm
        self.camera = camera
        self.scan_poses = scan_poses
        self.frames_dir = frames_dir  # куди зберігати кадри (debug); None — не зберігати
        self._scans = 0  # скільки оглядів зроблено за запуск

    def scan(self, request: RecheckRequest | None = None) -> list[Frame]:
        """Огляд усієї дошки згори або перегляд того, що просить детектор."""
        if request is None:
            log.info("scan: board")
            frames = [self.camera.capture() for _ in self.scan_poses or [[]]]
        else:
            reasons = ", ".join(r.value for r in request.reasons)
            log.info(f"scan: clearings {request.clearings} ({reasons})")
            frames = [self.camera.capture() for _ in request.clearings]
        self._scans += 1
        if self.frames_dir is not None:
            self._save(frames, request)
        return frames

    def _save(self, frames: list[Frame], request: RecheckRequest | None) -> None:
        """Кадри огляду + опис (debug)."""
        assert self.frames_dir is not None
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        name = f"scan_{self._scans:03d}"
        for i, frame in enumerate(frames):
            path = self.frames_dir / f"{name}_frame_{i}.jpg"
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        meta = {
            "reasons": ["board"] if request is None else [r.value for r in request.reasons],
            "clearings": [] if request is None else [int(c) for c in request.clearings],
            "frames": len(frames),
            "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        }
        (self.frames_dir / f"{name}.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
        )
        log.debug(f"scan: {len(frames)} frames saved to {self.frames_dir}/{name}_*")


def create_scanner(cfg: dict, arm: Arm, camera: Camera, frames_dir: Path | None = None) -> Scanner:
    return Scanner(arm, camera, cfg["camera"]["scan_poses"], frames_dir)
