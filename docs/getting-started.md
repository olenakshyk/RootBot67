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

# Тільки ігрова логіка й тести:
pip install -e ".[dev]"

# Усе разом (камера + робот):
pip install -e ".[dev,vision,robot]"

# Пайплайн датасету й тренування детектора (потрібен NVIDIA GPU), див. [ml.md](ml.md):
pip install -e ".[train]"

pytest
```

Запуск **без робота і без камери** (робот замінений на MockArm, кадри читаються з теки),
коли з'явиться `app/main.py`:

```bash
python -m rootbot.app.main --mock-robot --mock-camera data/samples
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
├── ml/                     # Пайплайн датасету й тренування детектора, поза пакетом робота
├── config/                 # YAML-конфіги: IP робота, камера, калібрування, модель мапи
├── scripts/                # Утиліти: калібрування, збір датасету, ручне керування рукою
├── tests/                  # pytest-тести (структура повторює src/rootbot), сценарії бота в tests/game/scenarios/
├── hardware/               # CAD пальців захвату, кріплення камери, схема розміщення
├── docs/                   # Документація проєкту
├── data/                   # Скани, датасети, проміжні результати (не в git), див. [ml.md](ml.md)
├── runs/                   # Запуски тренувань (не в git)
└── models/                 # Натреновані ваги моделей (не в git)
```

Чому один репозиторій, а не git-сабмодулі: [decisions/0001-monorepo.md](decisions/0001-monorepo.md).

## Конфігурація

Усі параметри — у `config/default.yaml` (коментар біля кожного). Свої значення пиши в `config/local.yaml`: лише те,
що змінюєш, решта береться з `default.yaml`.

Інший файл замість `default.yaml`: `python -m rootbot.app.main --config <файл>`. Тоді `local.yaml` не накладається.

