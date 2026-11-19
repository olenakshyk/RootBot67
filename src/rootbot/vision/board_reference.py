"""Еталон дошки: вирівняне зображення з відомим масштабом (docs/vision.md -> V1).

Система координат еталона: піксель (u, v) відповідає точці дошки (u / px_per_mm, v / px_per_mm) мм,
початок (0, 0) — лівий верхній кут знімка.
"""

import cv2
import numpy as np


def to_scale(img: np.ndarray, size_mm: tuple[float, float], px_per_mm: float) -> np.ndarray:
    """Змінює розмір знімка так, щоб на 1 мм дошки size_mm (ширина, висота) було px_per_mm пікселів.

    Знімок має вже бути вирівняний (строго зверху, обрізаний по краях дошки).
    INTER_AREA — усереднення пікселів, найкраще для зменшення (без «драбинок» і муару).
    """
    w = int(round(size_mm[0] * px_per_mm))
    h = int(round(size_mm[1] * px_per_mm))
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
