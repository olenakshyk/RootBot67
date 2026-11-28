"""Пошук дошки на кадрі без маркерів (V1): ключові точки кадру ↔ еталон дошки -> BoardHomography.

Зовнішній опис і вимоги: docs/vision.md -> V1, docs/requirements/detector.md (FR-V1..V5).

Еталон (data/reference/<name>.png + .yaml) — вирівняний знімок дошки з відомим масштабом px_per_mm,
його робить scripts/make_board_reference.py.
Піксель еталона (u, v) = точка дошки (u, v) / px_per_mm мм.

Як шукаємо:
1. SIFT: характерні точки й дескриптори еталона (раз, при створенні) і кадру;
2. для кожної точки кадру — два найближчі дескриптори еталона; тест Лоу (ratio_test) відкидає
   неоднозначні пари;
3. RANSAC-гомографія кадр -> мм дошки; пар, що з нею узгоджуються (inliers), має бути не менше
   min_inliers, і гомографія не вироджена — інакше «дошку не знайдено» (None).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import yaml

from rootbot.common.errors import FatalError
from rootbot.common.types import BoardPoint
from rootbot.config import ROOT
from rootbot.vision.camera import Frame

Matrix = npt.NDArray[np.float64]


@dataclass(frozen=True)
class BoardHomography:
    """Як на одному кадрі видно дошку."""

    to_board: Matrix  # 3x3: піксель кадру -> мм дошки
    frame_size: tuple[int, int]  # ширина, висота кадру, px
    board_size_mm: tuple[float, float]  # ширина, висота дошки, мм
    inliers: int = 0  # скільки пар кадр ↔ еталон узгоджуються з гомографією (якість)

    def to_board_mm(self, x: float, y: float) -> BoardPoint:
        """Піксель кадру -> точка на дошці, мм."""
        p = self.to_board @ np.array([x, y, 1.0])
        return BoardPoint(float(p[0] / p[2]), float(p[1] / p[2]))

    @cached_property
    def to_frame(self) -> Matrix:
        """3x3: мм дошки -> піксель кадру (обернена до to_board, рахується один раз)."""
        return np.linalg.inv(self.to_board)

    def to_frame_px(self, point: BoardPoint) -> tuple[float, float]:
        """Точка дошки, мм -> піксель кадру."""
        p = self.to_frame @ np.array([point.x, point.y, 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

    def visible(self) -> list[BoardPoint]:
        """Межі кадру на дошці (чотирикутник у мм): яку частину дошки видно."""
        w, h = self.frame_size
        return [self.to_board_mm(x, y) for x, y in ((0, 0), (w, 0), (w, h), (0, h))]

    def sees(self, center: BoardPoint, radius_mm: float = 0.0) -> bool:
        """Чи видно коло (наприклад, галявину) на кадрі цілком."""
        x, y = self.to_frame_px(center)
        w, h = self.frame_size
        if not (0 <= x <= w and 0 <= y <= h):
            return False
        # відстань від центру до межі видимої частини дошки, мм (> 0 — всередині)
        edge = cv2.pointPolygonTest(
            np.array([(p.x, p.y) for p in self.visible()], dtype=np.float32),
            (center.x, center.y),
            measureDist=True,
        )
        return bool(edge >= radius_mm)


class BoardDetector:
    """Шукає одну дошку (або планшет бота) за її еталоном."""

    def __init__(
        self,
        reference: npt.NDArray[np.uint8],
        px_per_mm: float,
        *,
        ratio_test: float,
        ransac_reproj_px: float,
        min_inliers: int,
        min_homography_det: float,
    ) -> None:
        self.px_per_mm = px_per_mm
        h, w = reference.shape[:2]
        self.board_size_mm = (w / px_per_mm, h / px_per_mm)
        self.ratio_test = ratio_test
        self.ransac_reproj_px = ransac_reproj_px
        self.min_inliers = min_inliers
        self.min_homography_det = min_homography_det

        # ? the main algorithm to find and encode visual features
        # ? (Scale-Invariant Feature Transform)
        self._sift: Any = cv2.SIFT_create()  # type: ignore[attr-defined]

        # ? just brute force with euclidean norm compare
        self._matcher: Any = cv2.BFMatcher(cv2.NORM_L2)
        self._ref_points, self._ref_descriptors = self._features(reference)
        if self._ref_descriptors is None or len(self._ref_points) < min_inliers:
            raise FatalError("board reference: too few keypoints (blank or too small image?)")

    def find(self, frame: Frame) -> BoardHomography | None:
        """Дошка на кадрі або None, якщо не знайдено."""
        points, descriptors = self._features(frame)
        if descriptors is None or len(points) < self.min_inliers:
            return None
        pairs = self._matcher.knnMatch(descriptors, self._ref_descriptors, k=2)

        # ? it is Lowe's ratio test for goodness of matching
        good = [
            m[0] for m in pairs if len(m) == 2 and m[0].distance < self.ratio_test * m[1].distance
        ]
        if len(good) < self.min_inliers:
            return None
        src_points = np.array([points[m.queryIdx] for m in good], dtype=np.float32)
        dst_points = np.array([self._ref_points[m.trainIdx] for m in good], dtype=np.float32)

        # ? so for now we found the same points at query and train frames,
        # ? now the first is transformed into the second by some H
        found, mask = cv2.findHomography(src_points, dst_points, cv2.RANSAC, self.ransac_reproj_px)
        if found is None:
            return None
        to_ref = np.asarray(found, dtype=np.float64)
        if (
            int(mask.sum()) < self.min_inliers
            or abs(np.linalg.det(to_ref)) < self.min_homography_det
        ):
            return None
        to_board = np.diag([1 / self.px_per_mm, 1 / self.px_per_mm, 1.0]) @ to_ref
        h, w = frame.shape[:2]
        return BoardHomography(to_board, (w, h), self.board_size_mm, int(mask.sum()))

    def _features(self, image: npt.NDArray[np.uint8]) -> tuple[list[tuple[float, float]], Any]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        keypoints, descriptors = self._sift.detectAndCompute(gray, None)
        return [kp.pt for kp in keypoints], descriptors


def create_board_detector(cfg: dict) -> BoardDetector:
    """Еталон з config board.reference_image (+ .yaml поруч), параметри з board.detector."""
    image_path = ROOT / cfg["board"]["reference_image"]
    meta_path = Path(image_path).with_suffix(".yaml")
    reference = cv2.imread(str(image_path))
    if reference is None or not meta_path.exists():
        raise FatalError(
            f"board.reference_image: no reference {image_path} (+ .yaml); "
            "make it with scripts/make_board_reference.py"
        )
    meta = yaml.safe_load(meta_path.read_text())
    params = cfg["board"]["detector"]
    return BoardDetector(
        np.asarray(reference, dtype=np.uint8),
        float(meta["px_per_mm"]),
        ratio_test=params["ratio_test"],
        ransac_reproj_px=params["ransac_reproj_px"],
        min_inliers=params["min_inliers"],
        min_homography_det=params["min_homography_det"],
    )
