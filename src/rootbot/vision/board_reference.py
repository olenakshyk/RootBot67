"""Еталон дошки: вирівняне зображення з відомим масштабом (docs/vision.md -> V1).

Система координат еталона: піксель (u, v) відповідає точці дошки (u / px_per_mm, v / px_per_mm) мм,
початок (0, 0) — лівий верхній кут знімка.

Орієнтація: лого ROOT праворуч угорі, доріжка рахунку внизу (X праворуч, Y донизу) — так само,
як задані центри галявин у config/maps/autumn.yaml. SIFT не зважає на поворот, тож еталон, знятий
повернутим на 180°, дошку знайде, але галявини мовчки з'їдуть у дзеркальні місця
(x' = 570 - x, y' = 440 - y). Найнадійніше робити еталон з того самого фото, що й центри галявин
(root_materials/root_board_photo/IMG_20261106_114643.jpg), у тій самій орієнтації.
Для фото під кутом (warp_to_board) орієнтацію задає порядок 4 кутів, а не поворот фото.
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


Corners = list[tuple[float, float]]


def warp_to_board(
    img: np.ndarray, corners_px: Corners, size_mm: tuple[float, float], px_per_mm: float
) -> np.ndarray:
    """Фото дошки під кутом -> вид строго зверху, обрізаний по краях, з масштабом px_per_mm.

    corners_px — 4 кути дошки на фото по порядку (0, 0) -> (W, 0) -> (W, H) -> (0, H), тобто
    лівий верхній, правий верхній (біля лого ROOT), правий нижній, лівий нижній. Порядок кутів
    і задає орієнтацію еталона, тож фото може бути повернуте як завгодно.
    """
    src = np.float32(corners_px)
    # Спершу розтягуємо в прямокутник приблизно того розміру, який дошка має на фото,
    # а вже потім зменшуємо to_scale (INTER_AREA): warpPerspective вміє лише інтерполяцію,
    # і одразу зменшене в кілька разів фото вийшло б з муаром.
    w = round(max(np.linalg.norm(src[1] - src[0]), np.linalg.norm(src[2] - src[3])))
    h = round(w * size_mm[1] / size_mm[0])
    rect = np.float32([(0, 0), (w, 0), (w, h), (0, h)])
    straight = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, rect), (w, h))
    return to_scale(straight, size_mm, px_per_mm)
