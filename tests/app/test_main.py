import cv2
import numpy as np
import pytest
import yaml

from rootbot.app import main as main_mod
from rootbot.app.operator_ui import OperatorServer
from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import load_config
from rootbot.robot.gripper import MockGripper

CFG = {"startup": {"retries": 2, "retry_delay_s": 0}}


def test_retry_then_ok():
    calls = []

    def create():
        calls.append(1)
        if len(calls) < 3:
            raise TemporaryError("ще не готово")
        return "ok"

    assert main_mod.create_with_retry("x", create, CFG) == "ok"
    assert len(calls) == 3


def test_retry_gives_up():
    def create():
        raise TemporaryError("не відповідає")

    with pytest.raises(FatalError, match="не відповідає"):
        main_mod.create_with_retry("x", create, CFG)


def test_fatal_is_not_retried():
    calls = []

    def create():
        calls.append(1)
        raise FatalError("немає файла")

    with pytest.raises(FatalError):
        main_mod.create_with_retry("x", create, CFG)
    assert len(calls) == 1


def _config(tmp_path, **overrides):
    cfg = load_config()
    cfg["robot"]["mock"] = True
    cfg["camera"]["source"] = "folder"
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "f1.jpg").write_bytes(b"")
    cfg["camera"]["folder"] = str(frames)
    cfg["logging"]["dir"] = str(tmp_path / "logs")
    reference = tmp_path / "board.png"  # еталон дошки: будь-яка текстура з ключовими точками
    texture = np.random.default_rng(0).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    cv2.imwrite(str(reference), cv2.GaussianBlur(texture, (5, 5), 0))
    reference.with_suffix(".yaml").write_text("px_per_mm: 1\n")
    cfg["board"]["reference_image"] = str(reference)
    for key, value in overrides.items():
        section, name = key.split(".")
        cfg[section][name] = value
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True))
    return path


def _server_with_end_game(cfg):
    server = OperatorServer(die_max=cfg["game"]["die_max"])
    server.end_game()  # інакше Orchestrator чекав би сигналів вічно
    return server


def test_main_starts_with_mocks(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "create_operator_server", _server_with_end_game)
    assert main_mod.main(["--config", str(_config(tmp_path))]) == 0


def test_main_stops_on_fatal(tmp_path):
    assert main_mod.main(["--config", str(_config(tmp_path, **{"robot.mock": False}))]) == 1


class StuckArm:
    def home(self) -> None:
        raise RuntimeError("arm stuck")


def test_home_failure_at_exit_is_reported():
    server = OperatorServer(die_max=6)
    main_mod.home_or_report(StuckArm(), server)
    assert server.current_state().error == "arm home failed: arm stuck"


class RecordingGripper:
    """Фейковий гріппер: пише виклики; fail — назви кроків, що кидають помилку."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def _record(self, call: str) -> None:
        self.calls.append(call)
        if call in self.fail:
            raise RuntimeError(f"{call} broken")

    def connect(self) -> None:
        self._record("connect")

    def disconnect(self) -> None:
        self._record("disconnect")

    def set_position(self, position: float) -> float:
        self._record(f"set_position {position}")
        return position


def test_main_connects_gripper_passes_it_to_manipulator_and_releases_at_exit(tmp_path, monkeypatch):
    gripper = RecordingGripper()
    manipulator_grippers = []
    real_create_manipulator = main_mod.create_manipulator

    def create_manipulator(cfg, arm, grip):
        manipulator_grippers.append(grip)
        return real_create_manipulator(cfg, arm, grip)

    monkeypatch.setattr(main_mod, "create_gripper", lambda cfg: gripper)
    monkeypatch.setattr(main_mod, "create_manipulator", create_manipulator)
    monkeypatch.setattr(main_mod, "create_operator_server", _server_with_end_game)

    assert main_mod.main(["--config", str(_config(tmp_path))]) == 0
    assert manipulator_grippers == [gripper]
    assert gripper.calls == ["connect", f"set_position {main_mod.GRIPPER_OPEN}", "disconnect"]


def test_main_creates_mock_gripper_from_config(tmp_path, monkeypatch):
    grippers = []
    real_create_manipulator = main_mod.create_manipulator

    def create_manipulator(cfg, arm, grip):
        grippers.append(grip)
        return real_create_manipulator(cfg, arm, grip)

    monkeypatch.setattr(main_mod, "create_manipulator", create_manipulator)
    monkeypatch.setattr(main_mod, "create_operator_server", _server_with_end_game)

    assert main_mod.main(["--config", str(_config(tmp_path))]) == 0
    assert isinstance(grippers[0], MockGripper)
    assert grippers[0].get_position() == main_mod.GRIPPER_OPEN


def test_gripper_failures_at_exit_are_reported_and_do_not_stop_release():
    server = OperatorServer(die_max=6)
    gripper = RecordingGripper(fail=(f"set_position {main_mod.GRIPPER_OPEN}", "disconnect"))
    main_mod.release_or_report(gripper, server)
    assert gripper.calls == [f"set_position {main_mod.GRIPPER_OPEN}", "disconnect"]
    assert server.current_state().error == "gripper disconnect failed: disconnect broken"


def test_gripper_open_failure_at_exit_is_reported(caplog):
    server = OperatorServer(die_max=6)
    gripper = RecordingGripper(fail=(f"set_position {main_mod.GRIPPER_OPEN}",))
    main_mod.release_or_report(gripper, server)
    assert gripper.calls == [f"set_position {main_mod.GRIPPER_OPEN}", "disconnect"]
    assert (
        server.current_state().error
        == f"gripper open failed: set_position {main_mod.GRIPPER_OPEN} broken"
    )
    assert "gripper open failed" in caplog.text


def test_gripper_failure_at_exit_keeps_fatal_exit_code(tmp_path, monkeypatch):
    gripper = RecordingGripper(fail=(f"set_position {main_mod.GRIPPER_OPEN}", "disconnect"))
    monkeypatch.setattr(main_mod, "create_gripper", lambda cfg: gripper)

    def broken_planner(cfg, board):
        raise FatalError("planner broken")

    monkeypatch.setattr(main_mod, "create_move_planner", broken_planner)
    assert main_mod.main(["--config", str(_config(tmp_path))]) == 1
    assert gripper.calls == ["connect", f"set_position {main_mod.GRIPPER_OPEN}", "disconnect"]
