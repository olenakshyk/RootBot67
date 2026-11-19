# Детектор фігурок: навчання й чесна оцінка

Дані: командні датасети `data/datasets/v6_2026-11-09` (реальні кадри) і `v7_simulation_2026-11-10` (10k синтетичних).
Класи - 12, як у `v6/classes.yaml`.

## Файли

- `make_lists.py` - списки для YOLO: train = усі train-кадри v6 (кадри, розмічені людиною, x3, як у команди),
  val і test = лише кадри, розмічені людиною (19 і 41), плюс test окремо по джерелах.
- `evaluate.py` - оцінка: `predict` (кеш рамок) і `score` (метрика команди P / R, точна кількість фігурок
  кожного класу на кадрі, 95 % межі бутстрепом по кадрах, розбивка по джерелах).
- `../../colab/train_detector_gpu.ipynb` - навчання на GPU в Colab (3 кандидати, вибір на val, test один раз).

## Правила чесної оцінки

1. Модель і поріг вибираємо **лише на val**. Правило вибору записане в ноутбуці наперед.
2. Test (41 кадр) запускаємо **один раз** для вибраної моделі.
3. Порівнюємо з `v5_all_s0` (test: P 0.969, R 0.920, mAP50 0.948, mAP50-95 0.58, `v6/meta/metrics.md`).
   Наша метрика P / R - відтворення опису з `metrics.md` (код команди в репозиторії немає), тому різниця
   1-2 пункти - не доказ. mAP рахує той самий ultralytics.
4. Кадрів мало: якщо межі 95 % двох моделей перекриваються, вважаємо їх рівними.

## Локально (CPU)

```
python scripts/detector/make_lists.py data/datasets/v6_2026-11-09 data/work/detector/lists
python scripts/detector/evaluate.py predict <ваги.pt> data/work/detector/lists/val.txt preds_val.json --imgsz 960 --tiles
python scripts/detector/evaluate.py score preds_val.json
```

## Дані на Kaggle для Colab (один раз)

```
mkdir -p /tmp/rootbot_upload && cp data/datasets/zips/*.zip /tmp/rootbot_upload/
echo '{"title": "rootbot-datasets", "id": "natalyakoz/rootbot-datasets", "licenses": [{"name": "other"}]}' > /tmp/rootbot_upload/dataset-metadata.json
KAGGLE_API_TOKEN=$(cat ~/.kaggle/access_token) uvx --with six kaggle datasets create -p /tmp/rootbot_upload
```

Датасет приватний (асети синтетики - лише для внутрішнього дослідження).

## Результати (2026-11-19, модель `models/rootbot_detector_v2_s960.pt`)

Навчено на CPU за ніч (GPU не було): YOLO11s з COCO, 12 епох, 960 px, train v6 (людські кадри x3).
Вибір лише на val з 14 варіантів (ваги best / last, дотренування 1280, ще 6 епох 960; інференс 960 / 1280, з тайлами / без),
правило: найбільше середнє P і R. Вибрано: last, 960, кадр + 2x2 тайли. Test запущено один раз.

| Test (41 кадр) | P | R | точна кількість | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- | --- |
| v2_s960 (ця) | 0.955 [0.937-0.970] | 0.881 [0.835-0.926] | 0.67 [0.60-0.74] | 0.878 | 0.497 |
| v5_all_s0 (команда, GPU, 1280, 40 еп.) | 0.969 | 0.920 | - | 0.948 | 0.580 |

У квадратних дужках - 95 % межі бутстрепом по кадрах. mAP - кадр цілком, 960 px (у v5_all_s0 - 1280 px).
По джерелах P / R: video_A 0.98 / 0.98, video_B 0.97 / 0.96, bgg 0.90 / 0.88, scan_s2 0.95 / 0.83, scan_s1 0.94 / 0.68.

Висновок: модель **не краща** за v5_all_s0 (R нижча, особливо дрібні фігурки на сканах). Причина - CPU:
12 епох на 960 px замість 40 на 1280. Дотренування на 1280 (3 епохи) і ще 6 епох на 960 на val гірші - відкинуто.
Наступний крок - `colab/train_detector_gpu.ipynb` (YOLO11m, 1280 / 1600, синтетика v7) на GPU.
