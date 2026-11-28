"""
python -m rootbot.app.main

- docs/architecture.md -> app -> main.

Що робить:
1. читає конфігурацію (config/default.yaml + config/local.yaml);
2. створює компоненти: камера, рука, Manipulator, Scanner, мапа, PieceDetectorModel (раз:
   ваги й прогрів), Detector, MovePlanner, OperatorServer;
3. передає їх Orchestrator і запускає його;
4. помилки створення: TemporaryError -> повтор (startup.retries),
   FatalError -> зупинка з повідомленням;
5. при будь-якому завершенні (кінець, помилка, Ctrl+C) рука йде в home.

Аргумент: --config <config>
Коди виходу: 0 — нормально, 1 — фатальна помилка, 130 — Ctrl+C.

Компоненти створюються тут і передаються в Orchestrator готовими, щоб Orchestrator
не залежав від того, справжня це рука чи мок, і щоб у тестах можна було підставити свої.
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from rootbot.app.move_planner import create_move_planner
from rootbot.app.operator_ui import OperatorServer, create_operator_server
from rootbot.app.orchestrator import Orchestrator
from rootbot.common.errors import FatalError, TemporaryError
from rootbot.config import ROOT, load_config
from rootbot.game.board import create_board
from rootbot.robot.arm import Arm, create_arm
from rootbot.robot.manipulator import create_manipulator
from rootbot.robot.scan import create_scanner
from rootbot.vision.camera import create_camera
from rootbot.vision.detector import create_detector
from rootbot.vision.piece_detector_model import create_piece_detector_model

log = logging.getLogger("rootbot")
T = TypeVar("T")


def setup_logging(cfg: dict) -> Path:
    """Лог у консоль і у файл logs/<date-time>.log"""

    log_dir = ROOT / cfg["logging"]["dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    logging.basicConfig(
        level=cfg["logging"]["level"].upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
        force=True,
    )
    return log_file


def create_with_retry(name: str, create: Callable[[], T], cfg: dict) -> T:
    """Створити компонент: TemporaryError -> повтор startup.retries разів, FatalError -> одразу."""

    retries = cfg["startup"]["retries"]
    for attempt in range(retries + 1):
        try:
            component = create()
            log.info(f"{name}: 🟢")
            return component
        except TemporaryError as e:
            if attempt == retries:
                raise FatalError(f"{name}: 🔴\n{e}") from e
            log.warning(f"{name}: 🟡 ({attempt + 1}/{retries})\n{e}")
            time.sleep(cfg["startup"]["retry_delay_s"])
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RootBot: Mechanical Marquise bot on xArm 7")
    ap.add_argument("--config", type=Path, help="config file instead of config/default.yaml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    log_file = setup_logging(cfg)
    log.info(f"start, log: {log_file}")

    arm = camera = server = None
    try:
        camera = create_with_retry("camera", lambda: create_camera(cfg), cfg)
        arm = create_with_retry("arm", lambda: create_arm(cfg), cfg)
        manipulator = create_with_retry("Manipulator", lambda: create_manipulator(cfg, arm), cfg)
        # debug: кадри сканера — у теку поруч із логом (logs/<час>/), щоб розбирати помилки
        debug = cfg["logging"]["level"].lower() == "debug"
        frames_dir = log_file.with_suffix("") if debug else None
        scanner = create_with_retry(
            "Scanner", lambda: create_scanner(cfg, arm, camera, frames_dir), cfg
        )
        board = create_with_retry("board", lambda: create_board(cfg), cfg)
        model = create_with_retry(
            "PieceDetectorModel", lambda: create_piece_detector_model(cfg), cfg
        )
        detector = create_with_retry("Detector", lambda: create_detector(cfg, board, model), cfg)
        planner = create_with_retry("MovePlanner", lambda: create_move_planner(cfg, board), cfg)
        server = create_with_retry("OperatorServer", lambda: create_operator_server(cfg), cfg)
        Orchestrator(
            arm=arm,
            scanner=scanner,
            detector=detector,
            board=board,
            planner=planner,
            manipulator=manipulator,
            server=server,
            cfg=cfg,
        ).run()
        return 0
    except FatalError as e:
        log.error(f"===ERROR===\n{e}")
        return 1
    except KeyboardInterrupt:
        log.info("===KeyboardInterrupt(Ctrl+C)===")
        return 130
    finally:
        if arm is not None:
            home_or_report(arm, server)
        log.info("===END===")


def home_or_report(arm: Arm, server: OperatorServer | None) -> None:
    """Рука в home наприкінці; збій руки лише в лозі й у статусі, код виходу не змінюється."""
    try:
        arm.home()
    except Exception as e:
        log.exception("arm home failed")
        if server is not None:
            server.set_status(error=f"arm home failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
