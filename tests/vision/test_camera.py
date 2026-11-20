"""RealSenseCamera з фейковим pyrealsense2, PipeWireCamera з фейковим процесом: без камери."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import load_config
from rootbot.vision.camera import (
    PipeWireCamera,
    RealSenseCamera,
    create_camera,
    create_realsense_camera,
    find_pipewire_node,
    pipewire_command,
)


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


# --- PipeWireCamera: замість gst-launch-1.0 — python, що пише кадри в stdout ---

W, H = 6, 4


def frames_command(n, *, delay=0.0, fail=None):
    """Процес, що пише n кадрів W×H (кадр i заповнений числом i), потім (за `fail`) — помилку."""
    script = (
        "import sys, time\n"
        f"for i in range(1, {n} + 1):\n"
        f"    sys.stdout.buffer.write(bytes([i]) * {W * H * 3}); sys.stdout.flush()\n"
        f"    time.sleep({delay})\n"
        + (f"sys.stderr.write({fail!r}); sys.exit(1)\n" if fail else "time.sleep(30)\n")
    )
    return [sys.executable, "-c", script]


def test_pipewire_warmup_and_fresh_frame():
    cam = PipeWireCamera(
        frames_command(20, delay=0.02), resolution=(W, H), warmup_frames=3, flush_frames=2
    )
    try:
        frame = cam.capture()
        assert frame.shape == (H, W, 3) and frame.dtype == np.uint8
        assert frame[0, 0, 0] >= 4 + 2  # після 4 кадрів старту ще flush_frames + 1
        frame[:] = 0  # копія, можна змінювати
    finally:
        cam.close()
    assert cam.process.poll() is not None


def test_pipewire_process_exit_is_temporary():
    with pytest.raises(TemporaryError, match="target not found"):
        PipeWireCamera(frames_command(0, fail="ERROR: target not found"), resolution=(W, H))


def test_pipewire_no_frame_is_temporary():
    cam = PipeWireCamera(frames_command(1), resolution=(W, H), timeout_ms=200)
    try:
        with pytest.raises(TemporaryError, match="no frame"):
            cam.capture()
    finally:
        cam.close()


def node(name, description, formats, media_class="Video/Source"):
    props = {"media.class": media_class, "node.name": name, "node.description": description}
    return {"info": {"props": props, "params": {"EnumFormat": [{"format": f} for f in formats]}}}


def test_find_pipewire_color_node():
    dump = [
        node("laptop", "Integrated Camera (V4L2)", ["YUY2"]),
        node("rs_depth", "Intel(R) RealSense(TM) Depth Camera 435 (V4L2)", []),
        node("rs_ir", "Intel(R) RealSense(TM) Depth Camera 435 (V4L2)", ["GRAY8", "UYVY"]),
        node("rs_color", "Intel(R) RealSense(TM) Depth Camera 435 (V4L2)", ["YUY2"]),
        {"type": "PipeWire:Interface:Link", "info": {}},
    ]
    assert find_pipewire_node(dump, "RealSense", "YUY2") == "rs_color"
    assert find_pipewire_node(dump[:3], "RealSense", "YUY2") is None


def test_pipewire_command():
    cmd = pipewire_command("v4l2_input.usb-0_5_1.3", (1920, 1080), 8)
    assert "target-object=v4l2_input.usb-0_5_1.3" in cmd
    assert "video/x-raw,width=1920,height=1080,framerate=8/1" in cmd
    assert cmd[-2:] == ["fdsink", "fd=1"]


def test_pipewire_has_no_manual_exposure():
    cfg = load_config()
    cfg["camera"].update(source="pipewire", exposure=150)
    with pytest.raises(FatalError, match="pipewire"):
        create_camera(cfg)
