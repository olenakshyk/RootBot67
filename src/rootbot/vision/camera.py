"""Камера: RealSense на руці або кадри з теки (для роботи без заліза).

create_camera(cfg) обирає за config: camera.source (realsense | folder) і camera.folder.
Решта коду знає лише Protocol Camera, а не конкретний клас.
"""

from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import ROOT

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

Frame = npt.NDArray[np.uint8]  # кадр BGR, як у OpenCV


class Camera(Protocol):
    """Що вміє будь-яка камера (RealSense, тека з кадрами, фейк у тестах)."""

    def capture(self) -> Frame: ...


class FolderCamera:
    """Кадри з теки замість камери: віддає файли по черзі."""

    def __init__(self, folder: Path) -> None:
        self.frames = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        self._next = 0

    def capture(self) -> Frame:
        """Наступний кадр з теки; після останнього знову перший."""
        import cv2  # optional dependency (vision)

        path = self.frames[self._next % len(self.frames)]
        self._next += 1
        image: Any = cv2.imread(str(path))
        if image is None:
            raise TemporaryError(f"cannot read frame {path}")
        return np.asarray(image, dtype=np.uint8)


def create_camera(cfg: dict) -> Camera:
    source = cfg["camera"].get("source", "realsense")
    if source == "folder":
        folder = ROOT / cfg["camera"]["folder"]
        if not folder.is_dir():
            raise FatalError(f"camera.folder: folder {folder} does not exist")
        camera = FolderCamera(folder)
        if not camera.frames:
            raise FatalError(f"camera.folder: no frames (.jpg / .png) in {folder}")
        return camera
    if source == "realsense":
        raise FatalError("RealSense camera is not implemented yet: set camera.source: folder")
    raise FatalError(f"camera.source: unknown value {source!r} (realsense | folder)")
