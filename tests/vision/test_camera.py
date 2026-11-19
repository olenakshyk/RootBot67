"""RealSenseCamera з фейковим pyrealsense2: справжня камера й SDK не потрібні."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import load_config
from rootbot.vision.camera import RealSenseCamera, create_camera, create_realsense_camera


class FakeSensor:
    def __init__(self):
        self.options = {"exposure": 156.0, "white_balance": 4600.0}

    def set_option(self, option, value):
        self.options[option] = value

    def get_option(self, option):
        return self.options[option]


class FakeColor:
    def __init__(self, n):
        self.n = n

    def __bool__(self):
        return True

    def get_data(self):
        return np.full((4, 6, 3), self.n, dtype=np.uint8)


class FakePipeline:
    def __init__(self, rs):
        self.rs = rs
        self.frames = 0  # скільки кадрів віддано
        self.stopped = False

    def start(self, config):
        if self.rs.fail_start:
            raise RuntimeError("No device connected")
        self.rs.config_used = config
        device = SimpleNamespace(first_color_sensor=lambda: self.rs.sensor)
        return SimpleNamespace(get_device=lambda: device)

    def wait_for_frames(self, timeout_ms):
        if self.rs.fail_frames:
            raise RuntimeError("Frame didn't arrive within 5000")
        self.frames += 1
        n = self.frames
        return SimpleNamespace(get_color_frame=lambda: FakeColor(n))

    def stop(self):
        self.stopped = True


class FakeConfig:
    def __init__(self):
        self.streams, self.device = [], None

    def enable_stream(self, *args):
        self.streams.append(args)

    def enable_device(self, serial):
        self.device = serial


def fake_rs(fail_start=False, fail_frames=False):
    rs = SimpleNamespace(fail_start=fail_start, fail_frames=fail_frames, sensor=FakeSensor())
    rs.pipeline = lambda: FakePipeline(rs)
    rs.config = FakeConfig
    rs.stream = SimpleNamespace(color="color")
    rs.format = SimpleNamespace(bgr8="bgr8")
    rs.option = SimpleNamespace(
        enable_auto_exposure="enable_auto_exposure",
        exposure="exposure",
        enable_auto_white_balance="enable_auto_white_balance",
        white_balance="white_balance",
    )
    return rs


def camera(rs, **kw):
    return RealSenseCamera(rs, resolution=(1920, 1080), fps=30, **kw)


def test_color_stream_and_warmup():
    rs = fake_rs()
    cam = camera(rs, serial="123", warmup_frames=5)
    assert rs.config_used.streams == [("color", 1920, 1080, "bgr8", 30)]
    assert rs.config_used.device == "123"
    assert cam.pipeline.frames == 5


def test_capture_drops_queued_frames_and_copies():
    rs = fake_rs()
    cam = camera(rs, flush_frames=3)
    frame = cam.capture()
    assert cam.pipeline.frames == 4
    assert frame.dtype == np.uint8 and frame.shape == (4, 6, 3)
    assert frame[0, 0, 0] == 4  # останній, свіжий кадр
    frame[:] = 0
    assert cam.capture()[0, 0, 0] == 8  # кадр — копія, а не буфер SDK


def test_auto_exposure_by_default():
    rs = fake_rs()
    camera(rs)
    assert "enable_auto_exposure" not in rs.sensor.options


def test_manual_exposure_and_white_balance():
    rs = fake_rs()
    cam = camera(rs, exposure=200, white_balance=4000)
    assert rs.sensor.options["enable_auto_exposure"] == 0
    assert rs.sensor.options["enable_auto_white_balance"] == 0
    assert cam.exposure() == {"exposure": 200.0, "white_balance": 4000.0}


def test_no_device_is_temporary():
    with pytest.raises(TemporaryError, match="USB 3"):
        camera(fake_rs(fail_start=True))


def test_no_frame_is_temporary():
    cam = camera(fake_rs())
    cam.rs.fail_frames = True
    with pytest.raises(TemporaryError, match="no frame"):
        cam.capture()


def test_created_from_config():
    cfg = load_config()
    cfg["camera"].update(exposure=150, warmup_frames=2, flush_frames=1)
    rs = fake_rs()
    cam = create_realsense_camera(cfg, rs)
    assert cam.flush_frames == 1 and cam.pipeline.frames == 2
    assert rs.sensor.options["exposure"] == 150.0


def test_without_pyrealsense2_is_fatal(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyrealsense2", None)  # import -> ImportError
    cfg = load_config()
    cfg["camera"]["source"] = "realsense"
    with pytest.raises(FatalError, match="pyrealsense2"):
        create_camera(cfg)
