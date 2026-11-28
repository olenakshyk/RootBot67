# Початок роботи

## Швидкий старт

Потрібен **Python 3.10+**.

```bash
git clone https://github.com/olenakshyk/RootBot67.git RootBot
cd RootBot

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

# Код і тести, як у CI (vision — OpenCV, потрібен тестам):
pip install -e ".[dev,vision]"

# Усе разом (камера + робот):
pip install -e ".[dev,vision,robot]"

# Модель фігурок YOLO (vision.piece_model.type: yolo, тягне torch):
pip install -e ".[ml-model]"

pytest
```

Запуск **без робота і без камери**: рука — `MockArm` (рухи лише пишуться в лог), кадри читаються з теки.
У `config/local.yaml`:

```yaml
robot:
  mock: true
camera:
  source: folder
  folder: data/samples   # свої кадри дошки (.jpg / .png)
```

Ще потрібні дві речі, яких у git немає:

- кадри дошки в `data/samples` (тека з `camera.folder`);
- еталон дошки `data/reference/board_autumn.png` разом з `board_autumn.yaml` поруч (`board.reference_image`).
  Його робить один раз скрипт `scripts/make_board_reference.py`
  ([vision.md → V1](vision.md#v1-дошка-й-планшет-бота)):

```bash
python scripts/make_board_reference.py --image <фото дошки згори> --name board_autumn
python -m rootbot.app.main
```

> ⚠️ Перед роботою зі **справжньою** рукою прочитайте [hardware-and-safety.md](hardware-and-safety.md).

## Структура репозиторію

```text
.
├── src/rootbot/          # Код робота (один пакет, модулі нижче)
│   ├── common/             # Спільні типи: масті, фракції, координати
│   ├── game/               # Модель гри й правила бота. Без заліза, повністю тестується
│   ├── vision/             # Камера, детекція дошки (без маркерів), фігурок, карт
│   ├── robot/              # Обгортка над xArm SDK, перетворення координат, перенесення фігурок, MockArm
│   └── app/                # Оркестратор ходу, інтерфейс оператора, точка входу
├── config/                 # YAML-конфіги: IP робота, камера, калібрування, модель мапи
├── scripts/                # Утиліти: калібрування, збір датасету, ручне керування рукою
├── tests/                  # pytest-тести (структура повторює src/rootbot), сценарії бота в tests/game/scenarios/
├── hardware/               # CAD пальців захвату, кріплення камери, схема розміщення
├── docs/                   # Документація проєкту
├── data/                   # Кадри, еталони дошки, датасети (не в git)
└── models/                 # Натреновані ваги моделей (не в git)
```

Чому один репозиторій, а не git-сабмодулі: [decisions/0001-monorepo.md](decisions/0001-monorepo.md).

## Конфігурація

Усі параметри — у `config/default.yaml` (коментар біля кожного). Свої значення пиши в `config/local.yaml`: лише те,
що змінюєш, решта береться з `default.yaml`.

Інший файл замість `default.yaml`: `python -m rootbot.app.main --config <файл>`. Тоді `local.yaml` не накладається.

