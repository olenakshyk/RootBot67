// Спільні частини екранів: напис, табло, банер, значки, кнопки.

import { h } from "preact";
import htm from "htm";

export const html = htm.bind(h);

// Ключі — значення Faction (common/types.py)
export const FACTIONS = {
  marquise_bot: { name: "Marquise bot", color: "var(--marquise)" },
  eyrie: { name: "Eyrie", color: "var(--eyrie)" },
  alliance: { name: "Alliance", color: "var(--alliance)" },
  vagabond: { name: "Vagabond", color: "var(--vagabond)" },
  riverfolk: { name: "Riverfolk", color: "var(--riverfolk)" },
  lizards: { name: "Lizards", color: "var(--lizards)" },
};
export const BOT = "marquise_bot";

export const factionName = (key) => FACTIONS[key]?.name ?? key;

export function Icon({ name, ...rest }) {
  return html`<svg viewBox="0 0 40 40" aria-hidden="true" ...${rest}><use href="#i-${name}" /></svg>`;
}

// Напис RootBot: ROOT у плитках мастей, BOT у сталевих (шестерня, кіт-Маркіза, рука)
const TILES = [
  ["R", "fox", "var(--fox)"],
  ["O", "rabbit", "var(--rabbit)"],
  ["O", "mouse", "var(--mouse)"],
  ["T", "bird", "var(--bird)"],
  null,
  ["B", "gear"],
  ["O", "cat"],
  ["T", "arm"],
];

export function Wordmark() {
  return html`
    <h1 class="wordmark torn" aria-label="RootBot">
      ${TILES.map((t, i) =>
        t
          ? html`<span
              key=${i}
              class=${t[2] ? "tile" : "tile steel"}
              style=${t[2] ? `--c:${t[2]}` : ""}
              aria-hidden="true"
            >
              <svg class="tile-art" viewBox="0 0 40 40"><use href="#i-${t[1]}" /></svg>
              <span class="glyph">${t[0]}</span>
            </span>`
          : html`<span key=${i} class="word-gap" aria-hidden="true"></span>`,
      )}
    </h1>
  `;
}

// Лавровий вінок навколо очок
const LEAVES = [-1, 1].flatMap((side) =>
  Array.from({ length: 6 }, (_, i) => {
    const a = ((110 + i * 24) * Math.PI) / 180;
    const x = (25 - side * Math.cos(a) * 21).toFixed(1);
    const y = (25 + Math.sin(a) * 21).toFixed(1);
    return { x, y, rot: side * (i * 24 + 20) };
  }),
);

export function Medal({ points }) {
  return html`
    <div class="medal">
      <svg viewBox="0 0 50 50" aria-hidden="true">
        ${LEAVES.map(
          (l, i) => html`<ellipse
            key=${i}
            cx=${l.x}
            cy=${l.y}
            rx="3"
            ry="6.5"
            transform="rotate(${l.rot} ${l.x} ${l.y})"
            fill="var(--laurel)"
            stroke="var(--ink)"
            stroke-width="1"
          />`,
        )}
        <circle cx="25" cy="25" r="16.5" fill="var(--parchment)" stroke="var(--gold)" stroke-width="3" />
        <circle cx="25" cy="25" r="18" fill="none" stroke="var(--ink)" stroke-width="1.4" />
      </svg>
      <span>${points}</span>
    </div>
  `;
}

// badge: підпис біля назви на фінальному табло («winner», «most points»)
export function ScoreItem({ faction, points, badge, botRef }) {
  const f = FACTIONS[faction] ?? { name: faction, color: "var(--muted)" };
  const cls = ["score", faction === BOT && "is-bot", badge && "leader"].filter(Boolean).join(" ");
  return html`
    <li class=${cls} style=${`--faction:${f.color}`} ref=${faction === BOT ? botRef : undefined}>
      <${Medal} points=${points} />
      <div class="bar torn">
        ${f.name}${badge && html`<small>${badge}</small>`}
        <span class="sr-only">: ${points} VP</span>
      </div>
    </li>
  `;
}

// Бот першим, далі як прийшли з сервера
export function sortedScores(scores) {
  return Object.entries(scores).sort(([a], [b]) => (b === BOT) - (a === BOT));
}

export function Scores({ scores, botRef }) {
  const entries = sortedScores(scores);
  if (!entries.length) return null;
  return html`
    <section aria-label="Scores">
      <ul class="scores">
        ${entries.map(
          ([k, pts]) => html`<${ScoreItem} key=${k} faction=${k} points=${pts} botRef=${botRef} />`,
        )}
      </ul>
    </section>
  `;
}

const PHASES = ["Birdsong", "Daylight", "Evening"];

// win: банер переможця, як на фінальному екрані гри (темна стрічка, лаврові гілки обабіч)
export function Banner({ title, phase, detail, alarm, win }) {
  const cls = ["banner", alarm && "alarm", win && "win", "torn"].filter(Boolean).join(" ");
  return html`
    <div class=${cls} role="status">
      ${win
        ? html`<h2>
            <svg class="sprig" viewBox="0 0 40 40" aria-hidden="true"><use href="#i-laurel" /></svg>
            <span>${title}</span>
            <svg class="sprig flip" viewBox="0 0 40 40" aria-hidden="true"><use href="#i-laurel" /></svg>
          </h2>`
        : html`<h2>${title}</h2>`}
      ${detail && html`<p class="detail">${detail}</p>`}
      ${phase &&
      html`<ol class="phase" aria-label="Bot turn phase">
        ${PHASES.map(
          (p) => html`<li key=${p} aria-current=${p === phase ? "step" : undefined}>${p}</li>`,
        )}
      </ol>`}
    </div>
  `;
}

// Що мають зробити люди (BotStatus.message): зняти фігурки, добрати карти
export function Todo({ message }) {
  if (!message) return null;
  // Orchestrator пише фракції ключами («eyrie: remove 2 …»): показуємо назви
  const text = message.replace(/\b[a-z_]+\b/g, (w) => FACTIONS[w]?.name ?? w);
  return html`
    <div class="todo torn" role="note">
      <${Icon} name="scroll" />
      <div>
        <h3>For the players</h3>
        <p>${text}</p>
      </div>
    </div>
  `;
}

export function ArmNote() {
  return html`
    <div class="arm-note">
      <${Icon} name="arm" />
      The arm may move. Keep your hands away from the board.
    </div>
  `;
}

// Кнопка, що шле сигнал: поки йде запит або бот ще не відповів, вона неактивна.
// aria-disabled замість disabled, щоб кнопка лишалась у фокусі й зчитувач чув новий підпис.
export function SendButton({ label, busyLabel, disabled, busy, inflight, class: cls = "primary", ...rest }) {
  const off = disabled || busy;
  return html`
    <button class="slab torn ${cls}" aria-disabled=${String(!!off)} ...${rest}>
      ${inflight ? "Sending…" : busy ? busyLabel : label}
    </button>
  `;
}
