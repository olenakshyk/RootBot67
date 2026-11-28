# Бібліотеки застосунку оператора

Готові ES-модулі з npm, без збірки ([ADR 0006](../../../../../docs/decisions/0006-operator-app.md)).
Шляхи `preact`, `preact/hooks`, `htm` прописані в import map у `../index.html`.

| Файл | Пакет | Звідки в пакеті | Ліцензія |
| --- | --- | --- | --- |
| `preact.js` | `preact@11.2.0` | `dist/preact.mjs` | MIT (`LICENSE-preact`) |
| `preact-hooks.js` | `preact@11.2.0` | `hooks/dist/hooks.mjs` | MIT (`LICENSE-preact`) |
| `htm.js` | `htm@3.1.1` | `dist/htm.mjs` | Apache-2.0 (`LICENSE-htm`) |

Оновити: `npm pack preact@<версія> htm@<версія>`, розпакувати й скопіювати ті самі файли.
Обидві частини Preact мають бути однієї версії.

## Шрифти (`fonts/`)

Латинські підмножини з Google Fonts (woff2), підключені в `../style.css` через `@font-face`. Лежать тут, а не
беруться з CDN, з тієї самої причини: сторінка має працювати без інтернету.

| Файл | Шрифт | Де в застосунку | Ліцензія |
| --- | --- | --- | --- |
| `grenze-gotisch-800.woff2` | Grenze Gotisch, 800 | напис RootBot у плитках | OFL 1.1 (`OFL-grenzegotisch.txt`) |
| `kurale-400.woff2` | Kurale | заголовки, кнопки, назви фракцій | OFL 1.1 (`OFL-kurale.txt`) |
| `alegreya-sans-{400,500,700}.woff2` | Alegreya Sans | основний текст | OFL 1.1 (`OFL-alegreyasans.txt`) |

Оновити: `https://fonts.googleapis.com/css2?family=<шрифт>` (з User-Agent сучасного браузера), узяти URL
блоку з `unicode-range: U+0000-00FF…`. Для українського інтерфейсу знадобиться ще кириличний блок
(у Grenze Gotisch кирилиці немає).
