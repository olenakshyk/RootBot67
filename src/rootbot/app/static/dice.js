// Бій (waiting_for = "dice", AUT-2, AUT-3): кубики кидає сам застосунок.
// Оператор (для Бродяги — спершу його мечі) натискає «Roll the dice», два 12-гранні кубики котяться по лотку,
// і щойно обидва впали, результат сам іде боту (POST /signals/dice). Перекинути не можна: кидок один, як за столом.
// Хто скільки влучив, рахує бот (game/marquise_bot.resolve_battle).

import { useEffect, useRef, useState } from "preact/hooks";
import { BOT, FACTIONS, factionName, html } from "./ui.js";

const SWORDS = [0, 1, 2, 3, 4, 5, 6, 7]; // непошкоджені мечі Бродяги; більше в партії не буває
const ROLL_MS = 1500; // скільки котиться перший кубик
const STAGGER_MS = 260; // другий падає трохи пізніше
const LANDED_PAUSE_MS = 700; // показати, що випало, перш ніж надіслати

// Кубик бою Root: 12 граней, по три на кожне значення 0..3, тобто рівномірно 0..3.
// crypto, а не Math.random: 256 ділиться на 4, тож без перекосу.
function rollDie() {
  const byte = new Uint8Array(1);
  crypto.getRandomValues(byte);
  return byte[0] % 4;
}

// Додекаедр спереду: десятикутний контур, п'ятикутна грань посередині й п'ять граней навколо
const R = 46;
const r = 25;
const pt = (radius, deg) => {
  const a = (deg * Math.PI) / 180;
  return [50 + radius * Math.cos(a), 50 + radius * Math.sin(a)];
};
const OUTER = Array.from({ length: 10 }, (_, i) => pt(R, -90 + i * 36));
const INNER = Array.from({ length: 5 }, (_, i) => pt(r, -90 + i * 72));
const poly = (pts) => pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");
const FACETS = INNER.map((p, i) =>
  poly([p, INNER[(i + 1) % 5], OUTER[(2 * i + 2) % 10], OUTER[2 * i + 1], OUTER[2 * i]]),
);
const SHADES = ["#e9dcb8", "#dccb9f", "#cfbb8a", "#d6c393", "#e3d4ab"]; // світло зліва згори

function D12({ value, rolling, index, onLanded }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(null);

  useEffect(() => {
    if (!rolling) return;
    const duration = ROLL_MS + index * STAGGER_MS;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(value);
      onLanded();
      return;
    }
    // числа мелькають, щораз повільніше, поки кубик котиться
    const start = performance.now();
    let timer = 0;
    const flicker = () => {
      const p = (performance.now() - start) / duration;
      if (p >= 0.82) return setShown(value);
      setShown(Math.floor(Math.random() * 4));
      timer = setTimeout(flicker, 45 + p * p * 260);
    };
    flicker();

    const dir = index ? 1 : -1;
    const spin = dir * (620 + Math.random() * 300);
    const x0 = -170 - index * 40;
    const anim = ref.current.animate(
      [
        { transform: `translate(${x0}px, -70px) rotate(${spin}deg) scale(0.85)`, offset: 0 },
        { transform: `translate(${x0 * 0.45}px, 6px) rotate(${spin * 0.45}deg) scale(1)`, offset: 0.38 },
        { transform: `translate(${x0 * 0.2}px, -26px) rotate(${spin * 0.18}deg)`, offset: 0.56 },
        { transform: `translate(${x0 * 0.06}px, 3px) rotate(${dir * 14}deg)`, offset: 0.74 },
        { transform: `translate(0, -6px) rotate(${-dir * 4}deg)`, offset: 0.87 },
        { transform: "translate(0, 0) rotate(0deg)", offset: 1 },
      ],
      { duration, easing: "cubic-bezier(0.25, 0.7, 0.35, 1)", fill: "both" },
    );
    anim.onfinish = () => {
      setShown(value);
      onLanded();
    };
    return () => {
      clearTimeout(timer);
      anim.onfinish = null;
    };
  }, [rolling]);

  const label = shown === null ? "Not rolled yet" : `Die shows ${shown}`;
  return html`
    <div class="die" ref=${ref} role="img" aria-label=${label}>
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <polygon points=${poly(OUTER)} fill="#c3ad79" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round" />
        ${FACETS.map(
          (f, i) => html`<polygon
            key=${i}
            points=${f}
            fill=${SHADES[i]}
            stroke="var(--ink)"
            stroke-width="1.6"
            stroke-linejoin="round"
          />`,
        )}
        <polygon points=${poly(INNER)} fill="#f6ecd2" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round" />
        <text x="50" y="62" text-anchor="middle" class="die-value">${shown ?? "?"}</text>
      </svg>
    </div>
  `;
}

function Plate({ faction }) {
  const f = FACTIONS[faction] ?? { name: faction, color: "var(--muted)" };
  return html`<div class="plate torn" style=${`--faction:${f.color}`}>${f.name}</div>`;
}

function Swords({ value, onChange, locked }) {
  return html`
    <fieldset class="swords">
      <legend>Vagabond's swords</legend>
      <p class="hint">Count the undamaged swords first: the Vagabond can't deal more hits than that.</p>
      <div class="faces" role="radiogroup" aria-label="Undamaged swords">
        ${SWORDS.map(
          (v) => html`<button
            key=${v}
            type="button"
            class="face"
            role="radio"
            aria-checked=${String(value === v)}
            aria-disabled=${String(locked)}
            onClick=${() => !locked && onChange(v)}
          >
            ${v}
          </button>`,
        )}
      </div>
    </fieldset>
  `;
}

// phase: ready -> rolling -> landed (результат іде боту)
export function DiceForm({ battle, sender }) {
  const [swords, setSwords] = useState(null);
  const [rolls, setRolls] = useState(null);
  const [phase, setPhase] = useState("ready");
  const landed = useRef(0);
  const vagabond = battle?.defender === "vagabond";
  const canRoll = phase === "ready" && (!vagabond || swords !== null) && !sender.busy;

  const body = () => ({ rolls, vagabond_swords: vagabond ? swords : null });

  const roll = () => {
    if (!canRoll) return;
    landed.current = 0;
    setRolls([rollDie(), rollDie()]);
    setPhase("rolling");
  };

  const onLanded = () => {
    landed.current += 1;
    if (landed.current === 2) setPhase("landed");
  };

  // обидва кубики впали: коротка пауза, і результат іде боту
  useEffect(() => {
    if (phase !== "landed") return;
    const t = setTimeout(() => sender.send("dice", body()), LANDED_PAUSE_MS);
    return () => clearTimeout(t);
  }, [phase]);

  let status = null;
  if (phase === "rolling") status = "Rolling…";
  else if (phase === "landed" && sender.inflight) status = `Rolled ${rolls[0]} and ${rolls[1]}. Sending to the bot…`;
  else if (phase === "landed" && sender.error) status = `Rolled ${rolls[0]} and ${rolls[1]}.`;
  else if (phase === "landed")
    status = `Rolled ${rolls[0]} and ${rolls[1]}. The bot works out the hits.`;

  return html`
    <div>
      ${battle &&
      html`<div class="versus" aria-label=${`Marquise bot attacks the ${factionName(battle.defender)}`}>
        <${Plate} faction=${BOT} />
        <span class="vs" aria-hidden="true">VS</span>
        <${Plate} faction=${battle.defender} />
      </div>`}
      ${vagabond && html`<${Swords} value=${swords} onChange=${setSwords} locked=${phase !== "ready"} />`}

      <div class="tray" aria-live="polite">
        <div class="tray-dice">
          ${[0, 1].map(
            (i) => html`<${D12}
              key=${i}
              index=${i}
              value=${rolls?.[i] ?? null}
              rolling=${phase !== "ready"}
              onLanded=${onLanded}
            />`,
          )}
        </div>
        <p class="tray-status">${status ?? "The site rolls both battle dice for you."}</p>
      </div>

      <div class="send-dock">
        ${phase === "landed" && sender.error
          ? html`<button
              type="button"
              class="slab torn primary"
              aria-disabled=${String(sender.busy)}
              onClick=${() => !sender.busy && sender.send("dice", body())}
            >
              Send the roll again
            </button>`
          : html`<button
              type="button"
              class="slab torn primary"
              aria-disabled=${String(!canRoll)}
              onClick=${roll}
            >
              ${phase === "ready" ? "Roll the dice" : phase === "rolling" ? "Rolling…" : "Rolled"}
            </button>`}
        ${vagabond && swords === null && html`<p class="hint send-hint">Pick the Vagabond's swords to roll.</p>`}
        ${sender.error && html`<p class="send-error" role="alert">${sender.error}</p>`}
      </div>
    </div>
  `;
}
