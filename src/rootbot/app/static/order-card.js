// Карта наказу (waiting_for = "order_card", AUT-1): масть і вартість крафту.
// Підказка «що зробить бот» повторює правила Дня з docs/game-rules.md (2.3); вирішує все одно бот.

import { useRef, useState } from "preact/hooks";
import { html, Icon, SendButton } from "./ui.js";

const SUITS = {
  fox: { name: "Fox", color: "var(--fox)" },
  rabbit: { name: "Rabbit", color: "var(--rabbit)" },
  mouse: { name: "Mouse", color: "var(--mouse)" },
  bird: { name: "Bird", color: "var(--bird)" },
};
const COST = ["fox", "rabbit", "mouse", "any"]; // CRAFT_SUITS
const MAX_COST = 4; // найбільша вартість крафту в колоді: 4 × «?»

const colorOf = (k) => (k === "any" ? "var(--any)" : SUITS[k].color);
const costName = (k) => (k === "any" ? "any" : k);

// Набір за вартістю крафту; warn — такої вартості в колоді немає.
// Без вартості крафту День починається заново з наступною картою, а якщо карт не лишилося — Вечір
// (cardsLeft — скільки карт Розкладу ще не відкрито, разом із цією; null — невідомо).
function recruitText(cost, cardsLeft) {
  if (cost.length === 0) {
    const after = cardsLeft === null ? null : cardsLeft - 1;
    if (after === 0)
      return { text: "No crafting cost and no cards left in the Schedule: the bot recruits nothing and moves on to Evening." };
    const next = after === null ? "" : ` (${after} ${after === 1 ? "card" : "cards"} left)`;
    return { text: `No crafting cost: the bot recruits nothing, then starts Daylight again with the next card${next}.` };
  }
  const kinds = new Set(cost);
  if (cost.length === 4 && kinds.size === 1 && kinds.has("any"))
    return { text: "Recruit: 4 warriors in the Keep clearing." };
  if (kinds.size === 1 && !kinds.has("any") && cost.length <= 3) {
    const n = cost.length;
    return { text: `Recruit: ${n} ${n === 1 ? "warrior" : "warriors"} in each ${cost[0]} clearing the bot rules.` };
  }
  if (cost.length === 3 && kinds.size === 3 && !kinds.has("any"))
    return { text: "Recruit: 1 warrior in each clearing the bot rules." };
  return { text: "No card in the deck has this crafting cost. Check the circles on the card.", warn: true };
}

function Forecast({ suit, cost, cardsLeft }) {
  if (!suit) return html`<div class="forecast"><p>Pick a suit to see what the bot will do this turn.</p></div>`;
  const r = recruitText(cost, cardsLeft);
  return html`
    <div class="forecast" aria-live="polite">
      <p>
        ${suit === "bird"
          ? html`Battle and move: in <b>all</b> clearings, because birds are wild.`
          : `Battle and move: in ${suit} clearings.`}
      </p>
      <p class=${r.warn ? "warn" : ""}>${r.text}</p>
    </div>
  `;
}

function CardPreview({ suit, cost, cardRef }) {
  const s = suit && SUITS[suit];
  return html`
    <div class="card-stage" aria-hidden="true">
      <div class=${suit ? "card face-up" : "card"} ref=${cardRef}>
        <div class="card-face" style=${s ? `--c:${s.color}` : ""}>
          <header>${s && html`<${Icon} name=${suit} />`}<span>${s?.name}</span></header>
          <div class="card-art">${s && html`<${Icon} name=${suit} />`}</div>
          <div class="card-cost">
            ${cost.length
              ? cost.map((k, i) => html`<b key=${i} style=${`--c:${colorOf(k)}`}><${Icon} name=${k} /></b>`)
              : html`<small>no cost</small>`}
          </div>
        </div>
        <div class="card-back"><span>Pick a suit</span></div>
      </div>
    </div>
  `;
}

// Карта «летить» до табло бота: єдина анімація на сторінці, у відповідь на надсилання
function flyCard(card, target) {
  if (!card || !target || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const from = card.getBoundingClientRect();
  const to = target.getBoundingClientRect();
  const ghost = card.cloneNode(true);
  ghost.classList.add("flying");
  Object.assign(ghost.style, { left: `${from.left}px`, top: `${from.top}px`, width: `${from.width}px`, margin: 0 });
  document.body.appendChild(ghost);
  requestAnimationFrame(() => {
    ghost.style.transform = `translate(${to.left + 10 - from.left}px, ${to.top - from.top}px) rotate(14deg) scale(0.2)`;
    ghost.style.opacity = "0.2";
  });
  setTimeout(() => ghost.remove(), 720);
}

export function OrderCardForm({ sender, botRef, cardsLeft = null }) {
  const [suit, setSuit] = useState(null);
  const [cost, setCost] = useState([]);
  const cardRef = useRef(null);
  const suitsRef = useRef(null);

  const invalid = !suit || recruitText(cost, cardsLeft).warn;

  const pickSuit = (k) => {
    setSuit(k);
    // фокус лишається на вибраній масті (roving tabindex)
    requestAnimationFrame(() => suitsRef.current?.querySelector(`[data-suit="${k}"]`)?.focus());
  };

  const onSuitKey = (e) => {
    const keys = Object.keys(SUITS);
    const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!d) return;
    e.preventDefault();
    const i = keys.indexOf(suit ?? "fox");
    pickSuit(keys[(i + d + keys.length) % keys.length]);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (invalid || sender.busy) return;
    const ok = await sender.send("order-card", { suit, craft_cost: cost });
    if (ok) flyCard(cardRef.current, botRef.current);
  };

  return html`
    <form onSubmit=${submit} novalidate>
      <div class="sheet-grid">
        <${CardPreview} suit=${suit} cost=${cost} cardRef=${cardRef} />

        <fieldset class="suit-field">
          <legend>Card suit</legend>
          <div class="suits" role="radiogroup" aria-label="Card suit" ref=${suitsRef} onKeyDown=${onSuitKey}>
            ${Object.entries(SUITS).map(
              ([k, s]) => html`
                <button
                  key=${k}
                  type="button"
                  class="suit"
                  role="radio"
                  data-suit=${k}
                  aria-checked=${String(suit === k)}
                  tabindex=${suit === k || (!suit && k === "fox") ? 0 : -1}
                  onClick=${() => pickSuit(k)}
                >
                  <span class="token" style=${`--c:${s.color}`}><${Icon} name=${k} /></span>
                  <span>${s.name}</span>
                </button>
              `,
            )}
          </div>
        </fieldset>

        <fieldset class="cost-field">
          <legend>Crafting cost</legend>
          <p class="hint">The circles in the card's bottom-left corner. Tap a circle here to remove it.</p>
          <div class="cost-row">
            <div class="slots" aria-live="polite">
              ${cost.map(
                (k, i) => html`<button
                  key=${`c${i}`}
                  type="button"
                  class="slot filled"
                  style=${`--c:${colorOf(k)}`}
                  aria-label=${`Remove ${costName(k)}`}
                  onClick=${() => setCost(cost.filter((_, j) => j !== i))}
                >
                  <${Icon} name=${k} />
                </button>`,
              )}
              ${Array.from(
                { length: MAX_COST - cost.length },
                (_, i) => html`<span key=${`e${i}`} class="slot" aria-hidden="true"></span>`,
              )}
            </div>
            ${cost.length > 0 && html`<button type="button" class="link" onClick=${() => setCost([])}>Clear</button>`}
          </div>
          <div class="adders">
            ${COST.map(
              (k) => html`<button
                key=${k}
                type="button"
                class="chip"
                disabled=${cost.length >= MAX_COST}
                onClick=${() => setCost([...cost, k])}
              >
                <i style=${`--c:${colorOf(k)}`}><${Icon} name=${k} /></i>${k === "any" ? "Any" : SUITS[k].name}
              </button>`,
            )}
          </div>
        </fieldset>

        <${Forecast} suit=${suit} cost=${cost} cardsLeft=${cardsLeft} />
      </div>

      <div class="send-dock">
        <${SendButton}
          type="submit"
          label="Send card"
          busyLabel="Card sent"
          inflight=${sender.inflight}
          disabled=${invalid}
          busy=${sender.busy}
        />
        ${sender.error && html`<p class="send-error" role="alert">${sender.error}</p>`}
      </div>
    </form>
  `;
}
