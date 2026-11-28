"""Камера: RealSense на руці або кадри з теки (для роботи без заліза).

create_camera(cfg) обирає за config: camera.source (realsense | pipewire | folder) і camera.folder.
Решта коду знає лише Protocol Camera, а не конкретний клас.

RealSense — через pyrealsense2 (extra `camera`; wheel-и лише до Python 3.14), лише кольоровий потік.
Та сама камера через PipeWire (Linux, GStreamer `pipewiresrc`) — без pyrealsense2 і працює на USB 2,
де librealsense кадрів не віддає; але без експозиції, балансу білого й заводських intrinsics.
Перевірити камеру й дошку на кадрі: scripts/check_camera.py.
"""

import json
import shutil
import subprocess
import tempfile
import threading
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


class PipeWireCamera:
    """Кольоровий потік через PipeWire (BGR): gst-launch-1.0 пише сирі кадри в stdout.

    Окремий потік безперервно читає кадри й тримає останній, тож черга не накопичується.
    warmup_frames / flush_frames — як у RealSenseCamera: capture() чекає flush_frames + 1 нових
    кадрів після виклику, щоб кадр точно був знятий уже після зупинки руки.
    """

    def __init__(
        self,
        command: list[str],
        *,
        resolution: tuple[int, int],
        warmup_frames: int = 0,
        flush_frames: int = 0,
        timeout_ms: int = 5000,
    ) -> None:
        self.command = command
        self.width, self.height = resolution
        self.flush_frames = flush_frames
        self.timeout_ms = timeout_ms
        self._frame: Frame | None = None
        self._count = 0  # скільки кадрів прочитано
        self._error: str | None = None
        self._new_frame = threading.Condition()
        self._stderr = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=self._stderr, stdin=subprocess.DEVNULL
            )
        except OSError as e:
            raise FatalError(f"PipeWire: cannot run {command[0]} ({e})") from e
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            self._wait(warmup_frames + 1)  # хоч один кадр: камера справді працює
        except TemporaryError:
            self.close()
            raise

    def capture(self) -> Frame:
        """Свіжий кадр: flush_frames + 1 кадрів, знятих після виклику."""
        self._wait(self.flush_frames + 1)
        with self._new_frame:
            assert self._frame is not None
            return self._frame.copy()  # copy: буфер з pipe лише для читання

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._reader.join(timeout=2)
        self._stderr.close()

    def _read(self) -> None:
        """Потік-читач: кадр за кадром зі stdout, поки процес не завершиться."""
        size = self.width * self.height * 3
        assert self.process.stdout is not None
        while True:
            data = self.process.stdout.read(size)
            # причину — до lock: _exit_reason чекає процес до 2 с, capture() стільки чекав би lock
            error = self._exit_reason() if len(data) < size else None
            with self._new_frame:
                if error is not None:
                    self._error = error
                else:
                    shape = (self.height, self.width, 3)
                    self._frame = np.frombuffer(data, dtype=np.uint8).reshape(shape)
                    self._count += 1
                self._new_frame.notify_all()
            if self._error is not None:
                return

    def _wait(self, frames: int) -> None:
        """Дочекатися ще `frames` кадрів; timeout_ms — на кожен кадр окремо."""
        with self._new_frame:
            target = self._count + frames
            while self._count < target:
                if self._error is not None:
                    raise TemporaryError(f"PipeWire: camera stopped ({self._error})")
                if not self._new_frame.wait(self.timeout_ms / 1000):
                    raise TemporaryError(f"PipeWire: no frame in {self.timeout_ms} ms")

    def _exit_reason(self) -> str:
        try:
            code = self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return "stdout closed"
        self._stderr.seek(0)
        lines = self._stderr.read().decode(errors="replace").strip().splitlines()
        return f"exit code {code}" + (f": {lines[-1]}" if lines else "")


def pipewire_command(node: str, resolution: tuple[int, int], fps: int) -> list[str]:
    """gst-launch-1.0: вузол PipeWire -> сирі кадри BGR у stdout."""
    width, height = resolution
    caps = f"video/x-raw,width={width},height={height},framerate={fps}/1"
    return [
        "gst-launch-1.0",
        "-q",
        "pipewiresrc",
        f"target-object={node}",
        "!",
        caps,
        "!",
        "videoconvert",
        "!",
        "video/x-raw,format=BGR",
        "!",
        "fdsink",
        "fd=1",
    ]


def find_pipewire_node(dump: list[dict], match: str, pixel_format: str) -> str | None:
    """Вузол-джерело відео з `match` у назві, що віддає `pixel_format` (у RealSense колір — YUY2).

    dump — вивід `pw-dump`. Назва вузла залежить від USB-порту, тому шукаємо щоразу.
    """
    for obj in dump:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Video/Source":
            continue
        if match not in props.get("node.description", ""):
            continue
        formats = (info.get("params") or {}).get("EnumFormat") or []
        if any(f.get("format") == pixel_format for f in formats):
            return props.get("node.name")
    return None


def create_pipewire_camera(cfg: dict) -> PipeWireCamera:
    c = cfg["camera"]
    p = c["pipewire"]
    if c.get("exposure") is not None or c.get("white_balance") is not None:
        raise FatalError(
            "camera.exposure / white_balance are not supported with camera.source: pipewire"
        )
    if shutil.which("gst-launch-1.0") is None:
        raise FatalError("gst-launch-1.0 not found: install GStreamer with the PipeWire plugin")
    node = p.get("node")
    if not node:
        if shutil.which("pw-dump") is None:
            raise FatalError("pw-dump not found: is PipeWire installed?")
        result = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10)
        node = find_pipewire_node(json.loads(result.stdout or "[]"), p["match"], p["format"])
        if node is None:
            raise TemporaryError(
                f"PipeWire: no video source {p['match']!r} with {p['format']}; "
                "is the camera connected? (wpctl status)"
            )
    width, height = c["resolution"]
    return PipeWireCamera(
        pipewire_command(node, (width, height), p["fps"]),
        resolution=(width, height),
        warmup_frames=c["warmup_frames"],
        flush_frames=c["flush_frames"],
        timeout_ms=c["timeout_ms"],
    )


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
    if source == "pipewire":
        return create_pipewire_camera(cfg)
    raise FatalError(f"camera.source: unknown value {source!r} (realsense | pipewire | folder)")


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
