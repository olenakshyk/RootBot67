"""Камера: RealSense на руці або кадри з теки (для роботи без заліза).

create_camera(cfg) обирає за config: camera.source (realsense | folder) і camera.folder.
Решта коду знає лише Protocol Camera, а не конкретний клас.

RealSense — через pyrealsense2 (extra `camera`; wheel-и лише до Python 3.14), лише кольоровий потік.
Перевірити камеру й дошку на кадрі: scripts/check_camera.py.
"""

from pathlib import Path
from types import ModuleType
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


class RealSenseCamera:
    """Кольоровий потік RealSense (BGR).

    Після старту відкидає warmup_frames кадрів, поки автоекспозиція підлаштовується.
    Перед кожним кадром відкидає flush_frames кадрів з черги: вони могли бути зняті ще в русі руки.
    exposure / white_balance: число — вручну (кадри з різних поз однакові), None — авто.
    """

    def __init__(
        self,
        rs: ModuleType,
        *,
        resolution: tuple[int, int],
        fps: int,
        serial: str | None = None,
        exposure: float | None = None,
        white_balance: float | None = None,
        warmup_frames: int = 0,
        flush_frames: int = 0,
        timeout_ms: int = 5000,
    ) -> None:
        self.rs = rs
        self.flush_frames = flush_frames
        self.timeout_ms = timeout_ms
        self.pipeline = rs.pipeline()
        config = rs.config()
        if serial:
            config.enable_device(serial)
        width, height = resolution
        config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        try:
            self.profile = self.pipeline.start(config)
        except RuntimeError as e:
            raise TemporaryError(
                f"RealSense: cannot start color stream {width}x{height}@{fps} ({e}); "
                "is the camera on USB 3 and are the udev rules installed?"
            ) from e
        self.sensor = self.profile.get_device().first_color_sensor()
        self._set_manual(rs.option.enable_auto_exposure, rs.option.exposure, exposure)
        self._set_manual(
            rs.option.enable_auto_white_balance, rs.option.white_balance, white_balance
        )
        for _ in range(warmup_frames):
            self._wait()

    def capture(self) -> Frame:
        """Свіжий кадр: спершу відкинути flush_frames кадрів з черги."""
        for _ in range(self.flush_frames):
            self._wait()
        color = self._wait().get_color_frame()
        if not color:
            raise TemporaryError("RealSense: frameset without a color frame")
        # copy: буфер кадру належить librealsense і перезаписується наступними кадрами
        return np.array(color.get_data(), dtype=np.uint8, copy=True)

    def info(self) -> dict[str, str]:
        """Модель, серійний номер, прошивка, USB."""
        device = self.profile.get_device()
        names = {
            "name": "name",
            "serial": "serial_number",
            "firmware": "firmware_version",
            "usb": "usb_type_descriptor",
        }
        return {
            key: device.get_info(getattr(self.rs.camera_info, attr))
            for key, attr in names.items()
            if device.supports(getattr(self.rs.camera_info, attr))
        }

    def exposure(self) -> dict[str, float]:
        """Поточні експозиція й баланс білого (щоб переписати авто-значення в конфіг)."""
        return {
            "exposure": float(self.sensor.get_option(self.rs.option.exposure)),
            "white_balance": float(self.sensor.get_option(self.rs.option.white_balance)),
        }

    def intrinsics(self) -> dict[str, Any]:
        """Заводські intrinsics кольорового потоку у вигляді OpenCV: матриця камери й дисторсія."""
        stream = self.profile.get_stream(self.rs.stream.color).as_video_stream_profile()
        i = stream.get_intrinsics()
        return {
            "resolution": [i.width, i.height],
            "camera_matrix": [[i.fx, 0.0, i.ppx], [0.0, i.fy, i.ppy], [0.0, 0.0, 1.0]],
            "dist_coeffs": [float(c) for c in i.coeffs],
            "model": str(i.model),
        }

    def close(self) -> None:
        self.pipeline.stop()

    def _set_manual(self, auto: Any, option: Any, value: float | None) -> None:
        if value is None:
            return
        self.sensor.set_option(auto, 0)
        self.sensor.set_option(option, float(value))

    def _wait(self) -> Any:
        try:
            return self.pipeline.wait_for_frames(self.timeout_ms)
        except RuntimeError as e:
            raise TemporaryError(f"RealSense: no frame in {self.timeout_ms} ms ({e})") from e


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
        try:
            import pyrealsense2 as rs  # optional dependency (camera)
        except ImportError as e:
            raise FatalError(
                "pyrealsense2 is not installed: pip install -e '.[camera]' (Python <= 3.14), "
                "or set camera.source: folder"
            ) from e
        return create_realsense_camera(cfg, rs)
    raise FatalError(f"camera.source: unknown value {source!r} (realsense | folder)")


def create_realsense_camera(cfg: dict, rs: ModuleType) -> RealSenseCamera:
    c = cfg["camera"]
    width, height = c["resolution"]
    return RealSenseCamera(
        rs,
        resolution=(width, height),
        fps=c["fps"],
        serial=c.get("serial"),
        exposure=c.get("exposure"),
        white_balance=c.get("white_balance"),
        warmup_frames=c["warmup_frames"],
        flush_frames=c["flush_frames"],
        timeout_ms=c["timeout_ms"],
    )
