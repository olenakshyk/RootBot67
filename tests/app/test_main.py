import cv2
import numpy as np
import pytest
import yaml

from rootbot.app import main as main_mod
from rootbot.app.operator_ui import OperatorServer
from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import load_config

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


def test_main_starts_with_mocks(tmp_path, monkeypatch):
    def server_with_end_game(cfg):
        server = OperatorServer(die_max=cfg["game"]["die_max"])
        server.end_game()  # інакше Orchestrator чекав би сигналів вічно
        return server

    monkeypatch.setattr(main_mod, "create_operator_server", server_with_end_game)
    assert main_mod.main(["--config", str(_config(tmp_path))]) == 0


def test_main_stops_on_fatal(tmp_path):
    assert main_mod.main(["--config", str(_config(tmp_path, **{"robot.mock": False}))]) == 1
