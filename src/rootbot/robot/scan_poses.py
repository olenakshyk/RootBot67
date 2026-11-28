"""Оглядові пози без калібрування: кандидати навколо початкової пози й вибір найкорисніших.

Калібрування дошка -> робот (#22) немає, тож де дошка відносно руки, невідомо. Тому кандидати —
зсуви й нахили початкової пози (з неї камера вже бачить дошку), а корисність кожного з них
перевіряє камера: яку частину дошки видно (BoardMask) і які галявини цілком.
Рух і зйомка — у scripts/explore_scan_poses.py; результат — бібліотека поз (camera.scan_poses_file),
з якої Scanner вибирає, куди стати (ScanPose, load_scan_poses).
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from rootbot.common.types import BoardPoint, ClearingId
from rootbot.robot.arm import JOINTS
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


def joint_step(a: list[float], b: list[float]) -> float:
    """Найбільший поворот суглоба між двома наборами кутів, градуси."""
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def in_workspace(pose: Pose, workspace: dict[str, list[float]]) -> bool:
    """Чи кінець руки в межах robot.workspace_mm (x, y, z, система основи)."""
    return all(
        lo <= v <= hi for v, (lo, hi) in zip(pose[:3], [workspace[k] for k in "xyz"], strict=True)
    )


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


@dataclass(frozen=True)
class ScanPose:
    """Оглядова поза з бібліотеки: куди стати і що звідти видно (за explore_scan_poses.py)."""

    name: str
    joints: list[float]  # кути суглобів, градуси
    sees: list[BoardPoint]  # видима частина дошки (чотирикутник, мм дошки)
    clearings: frozenset[ClearingId]  # галявини, видимі цілком


def load_scan_poses(path: Path) -> list[ScanPose]:
    """Бібліотека поз у порядку з файлу (перша — початкова).

    ValueError, якщо в позі не JOINTS кутів або назви повторюються (Scanner розрізняє їх за назвою).
    """
    data = yaml.safe_load(path.read_text()) or {}
    poses = [
        ScanPose(
            name=p["name"],
            joints=[float(j) for j in p["joints"]],
            sees=[BoardPoint(float(x), float(y)) for x, y in p["sees"]],
            clearings=frozenset(ClearingId(c) for c in p["clearings"]),
        )
        for p in data.get("poses") or []
    ]
    names = [p.name for p in poses]
    errors = [
        f"pose {p.name!r}: expected {JOINTS} joint angles, got {len(p.joints)}"
        for p in poses
        if len(p.joints) != JOINTS
    ]
    errors += [f"pose name {n!r} is repeated" for n in sorted(set(names)) if names.count(n) > 1]
    if errors:
        raise ValueError(f"{path}: " + "; ".join(errors))
    return poses
