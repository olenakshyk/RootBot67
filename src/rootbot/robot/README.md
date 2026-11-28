# `robot` — рука xArm 7

Огляд дошки камерою на руці й перенесення фігурок (`Manipulator`). Опис:
[docs/architecture.md → robot](../../../docs/architecture.md#модулі), рух і захват: [docs/robot.md](../../../docs/robot.md),
деталі — у docstring файлів.

- `gripper.py` — гріппер. Тип обирається з config `robot.gripper`: `mock` | `ufactory` | `somatic`.
  Зовнішній API — `create_gripper(cfg)` + `connect / get_position / set_position / disconnect`
  (як `Arm`, позиція 0.0..1.0). Створює й під'єднує `main`, передає в `Manipulator`.
  `ufactory` — стандартний гріппер UFACTORY через `xarm-python-sdk`;
  `somatic` — приватний драйвер `gripper_somatic.py` (внутрішній протокол Somatic, не входить у репо).
  Ручна перевірка: `python scripts/gripper_control.py --set 0.25`.
