# Крок 3: перевірка точок (інструкція для Claude-помічника або людини)

Частина [dataset-pipeline.md](dataset-pipeline.md). Пишемо англійською, бо цю інструкцію читає й Claude-помічник.

## Task

For each frame listed in `data/to_label/<batch>/` produce the final list of pieces: **one point per piece**, well
inside the piece body, with its class. Write `data/points/<batch>.json`:

```json
{"<frame_stem>": [{"x": 1234, "y": 567, "cls": "cat_warrior"}]}
```

- `x, y` are **full-resolution** pixels of `data/frames/<stem>.jpg`.
- If `data/points/<batch>.json` already exists, keep its frames and continue with the missing ones.
- Save after every 5 frames. Be brief: no long reasoning, no summaries.

## Inputs

- `data/candidates/candidates.json`: `{stem: [{"id","x","y","cls"}]}`, colour-detector guesses (full-res).
  They contain wrong classes, false positives (plants, player boards, office objects) and miss pieces
  (small/far ones, green warriors).
- `data/candidates/previews/<stem>.jpg`: half-size frame with numbered dots and a white grid. Grid labels are
  full-res coordinates (line every 200 px). Preview pixel × 2 = full-res pixel.
- Zoom (at most 2 crops per frame, ≤ 1000×700, only for dense clusters or doubts):
  `ffmpeg -v error -y -i data/frames/<stem>.jpg -vf "crop=W:H:X:Y" <scratch>/z.jpg`, then a point (u, v)
  in the crop is (X+u, Y+v). Only the Python standard library is available (`python3 -c`).

## Classes

The list and its order: `config/piece_classes.txt`.

| Class | What it is |
| --- | --- |
| `cat_warrior` | orange wooden warrior |
| `keep` | round brown/orange cardboard token with a castle; skip if unsure |
| `eyrie_warrior` | blue wooden warrior |
| `eyrie_roost` | dark blue square tile with a white branch |
| `alliance_warrior` | green wooden warrior |
| `alliance_base` | light-green square tile |
| `alliance_sympathy` | light-green **round** token (fist) |

Do **not** label: dice, cards, white square item tiles, score-track markers, brown square Marquise buildings,
plain brown round wood tokens, white or other figures, non-game objects. Skip pieces < ~10 px or too blurry.

## Rules

- Exactly one point per piece. In tight clusters each piece gets its own point on its own body, never between pieces.
- Keep a candidate if it sits well on one piece (fix the class), move it otherwise, drop false ones, add missed pieces.

## Final report

≤ 10 lines: frames done, points per class, unsure frames (stem + short reason).
