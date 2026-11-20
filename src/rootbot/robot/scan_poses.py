"""Оглядові пози без калібрування: кандидати навколо початкової пози й вибір найкорисніших.

Калібрування дошка -> робот (#22) немає, тож де дошка відносно руки, невідомо. Тому кандидати —
зсуви й нахили початкової пози (з неї камера вже бачить дошку), а корисність кожного з них
перевіряє камера: яку частину дошки видно (BoardMask) і які галявини цілком.
Рух і зйомка — у scripts/explore_scan_poses.py.
"""

from rootbot.vision.board_mask import BoardMask

Pose = list[float]  # x, y, z (мм), roll, pitch, yaw (градуси) у системі основи робота


def candidate_poses(
    start: Pose, *, shift_mm: float, lift_mm: float, tilt_deg: float
) -> list[tuple[str, Pose]]:
    """Початкова поза, її зсуви на ±shift по x і y, підйом на lift, нахили на ±tilt.

    Нижче початкової пози кандидати не опускаються: ближче до столу — ризик зачепити фігурки.
    """
    x, y, z, roll, pitch, yaw = start
    poses = [("start", list(start))]
    for dx in (-shift_mm, 0.0, shift_mm):
        for dy in (-shift_mm, 0.0, shift_mm):
            if dx or dy:
                poses.append(
                    (f"shift_x{dx:+.0f}_y{dy:+.0f}", [x + dx, y + dy, z, roll, pitch, yaw])
                )
    poses.append((f"lift_z{lift_mm:+.0f}", [x, y, z + lift_mm, roll, pitch, yaw]))
    for name, d_roll, d_pitch in (
        ("roll", tilt_deg, 0.0),
        ("roll", -tilt_deg, 0.0),
        ("pitch", 0.0, tilt_deg),
        ("pitch", 0.0, -tilt_deg),
    ):
        delta = d_roll or d_pitch
        poses.append((f"tilt_{name}{delta:+.0f}", [x, y, z, roll + d_roll, pitch + d_pitch, yaw]))
    return poses


def select_poses(
    masks: dict[str, BoardMask], clearings: dict[str, list[int]], keep: int, first: str
) -> list[str]:
    """Не більше keep поз: спершу `first`, далі жадібно ті, що додають найбільше видимої дошки.

    Коли нова площа вже не додається, решта місць — позам, що бачать цілком найбільше галявин:
    інший кут на ті самі галявини (для переглядів «disagree» / «low confidence»).
    """
    chosen = [first]
    covered = masks[first]
    rest = [name for name in masks if name != first]
    while rest and len(chosen) < keep:
        gain = {name: (masks[name] - covered).area() for name in rest}
        best = max(rest, key=lambda n: (gain[n], len(clearings[n])))
        if gain[best] == 0:
            best = max(rest, key=lambda n: len(clearings[n]))
            if not clearings[best]:
                break
        chosen.append(best)
        covered = covered | masks[best]
        rest.remove(best)
    return chosen
