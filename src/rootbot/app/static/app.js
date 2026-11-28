// Застосунок оператора (ADR 0006, #26): Preact + htm, без збірки.
// Раз на секунду питає GET /state і показує екран за станом бота:
//   waiting_for = "your_turn"  -> хід людей (або допомога після збою, якщо є error);
//   waiting_for = "order_card" -> карта наказу;   waiting_for = "dice" -> бій (кубики кидає застосунок);
//   waiting_for = null         -> бот ходить (або готується / кінець гри).
// Шляхи API: docs/requirements/operator-server.md -> HTTP API.

import { render } from "preact";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { signal, useBotState } from "./api.js";
import { DiceForm } from "./dice.js";
import { startForest } from "./forest.js";
import { setSkyTime } from "./sky.js";
import { OrderCardForm } from "./order-card.js";
import {
  BirdsongNotice,
  BotMoving,
  Connecting,
  EndGameDialog,
  GameOver,
  NeedsHelp,
  OtherSignals,
  phaseOf,
  PlayersTurn,
  Starting,
} from "./screens.js";
import { Banner, factionName, html, Scores, Todo, Wordmark } from "./ui.js";

const SENT_TIMEOUT_MS = 8000; // скільки чекати, поки бот відреагує на сигнал, перш ніж знову дати кнопку
const TOAST_MS = 3500;

function viewOf(state) {
  if (!state) return "connecting";
  if (state.step === "game over") return "game_over";
  if (state.notice?.kind === "birdsong") return "notice";
  if (state.waiting_for === "order_card") return "order_card";
  if (state.waiting_for === "dice") return "dice";
  if (state.waiting_for === "your_turn") return state.error ? "help" : "players";
  if (state.step === "starting") return "starting";
  return "moving";
}

// Надсилання сигналів. Після сигналу, що чекає відповіді бота, кнопки неактивні,
// доки стан не зміниться (або SENT_TIMEOUT_MS), щоб не надіслати двічі.
function useSender(state, offline, toast) {
  const [inflight, setInflight] = useState(false);
  const [error, setError] = useState(null);
  const [sentKey, setSentKey] = useState(null);
  const key = state ? `${state.step}|${state.waiting_for}|${state.error}` : null;

  useEffect(() => setError(null), [key]);
  useEffect(() => {
    if (sentKey === null) return;
    const t = setTimeout(() => setSentKey(null), SENT_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [sentKey]);

  // note: сигнал без відповіді бота (карта, Keep): лише сповіщення, кнопки не блокуються
  const send = async (name, body, note) => {
    if (offline) return false;
    setInflight(true);
    if (!note) setError(null);
    try {
      await signal(name, body);
      if (note) toast(note);
      else setSentKey(key);
      return true;
    } catch (e) {
      if (note) toast(e.message);
      else setError(e.message);
      return false;
    } finally {
      setInflight(false);
    }
  };

  return { send, inflight, error, busy: offline || inflight || (sentKey !== null && sentKey === key) };
}

function useToast() {
  const [toast, setToast] = useState(null);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);
  const show = useCallback((text) => setToast({ text, id: Date.now() }), []);
  return [toast, show];
}

function Main({ view, state, sender, botRef }) {
  switch (view) {
    case "connecting":
      return html`<${Connecting} />`;
    case "starting":
      return html`<${Starting} />`;
    case "players":
      return html`<${PlayersTurn} sender=${sender} />`;
    case "notice":
      return html`<${BirdsongNotice} notice=${state.notice} />`;
    case "order_card":
      return html`<${OrderCardForm} sender=${sender} botRef=${botRef} cardsLeft=${state.cards_left ?? null} />`;
    case "dice":
      return html`<${DiceForm} battle=${state.battle} sender=${sender} />`;
    case "help":
      return html`<${NeedsHelp} state=${state} sender=${sender} />`;
    case "game_over":
      return html`<${GameOver} state=${state} sender=${sender} />`;
    default:
      return html`<${BotMoving} state=${state} />`;
  }
}

// Пора доби на небі: фаза ходу бота; хід людей, підготовка й кінець гри — ніч
const PHASE_TIME = { Birdsong: "morning", Daylight: "day", Evening: "evening" };

function timeOf(view, state) {
  if (view === "order_card" || view === "dice") return "day";
  if (view === "moving" || view === "notice" || (view === "help" && state.step !== "turn aborted"))
    return PHASE_TIME[phaseOf(state.step)] ?? "night";
  return "night";
}

function bannerOf(view, state) {
  switch (view) {
    case "connecting":
      return { title: "Connecting" };
    case "starting":
      return { title: "Getting ready" };
    case "players":
      return { title: "Players' turn" };
    case "notice":
      return { title: "Birdsong", phase: "Birdsong" };
    case "order_card":
      return { title: "Order card", detail: dayAgain(state) ? "Daylight again" : null, phase: "Daylight" };
    case "dice":
      return {
        title: "Battle",
        detail: state.battle ? `Clearing ${state.battle.clearing}` : null,
        phase: "Daylight",
      };
    case "help":
      return state.step === "turn aborted"
        ? { title: "Turn stopped", alarm: true }
        : { title: "Needs a hand", phase: phaseOf(state.step), alarm: true };
    case "game_over":
      return state.winner ? { title: `${factionName(state.winner)} wins!`, win: true } : { title: "Game over" };
    default:
      return { title: "Bot's turn", phase: phaseOf(state.step) };
  }
}

// День заново (RepeatDay): попередня карта наказу була без вартості крафту
const dayAgain = (state) => state.step.includes("no craft cost");

function instructionOf(view, state) {
  if (view === "order_card" && dayAgain(state)) {
    const left = state.cards_left;
    const count = left == null ? "" : ` (${left} ${left === 1 ? "card" : "cards"} left)`;
    return html`That card had no crafting cost, so Daylight starts again. Reveal the <b>next</b> card in the
      Schedule${count} and mark it here.`;
  }
  if (view === "order_card")
    return html`Reveal the <b>leftmost</b> card in the Schedule of Orders and mark it here: suit and crafting cost.`;
  if (view === "dice")
    return state.battle
      ? `The bot attacks in clearing ${state.battle.clearing}. Roll the dice here: the result goes to the bot.`
      : "Roll the dice here: the result goes to the bot.";
  return null;
}

function App() {
  const { state, offline } = useBotState();
  const [toast, showToast] = useToast();
  const sender = useSender(state, offline, showToast);
  const [ending, setEnding] = useState(false);
  // Keep знищено: сервер каже keep_destroyed; локально — одразу після сигналу, до наступного опитування
  const [keepSent, setKeepSent] = useState(false);
  const botRef = useRef(null);

  const view = viewOf(state);
  const says = instructionOf(view, state);
  const playing = view !== "connecting" && view !== "game_over";

  // поки немає першого стану, небо не чіпаємо: інакше після кожного перезавантаження сонце
  // проходило б дугою від ночі до поточної пори
  const time = view === "connecting" ? null : timeOf(view, state);
  useEffect(() => {
    if (time) setSkyTime(time);
  }, [time]);

  // нова партія — новий Keep
  useEffect(() => {
    if (view === "game_over") setKeepSent(false);
  }, [view]);

  return html`
    <div class="app">
      <header class="topbar">
        <${Wordmark} />
        <div class=${offline ? "link-state off" : "link-state"} role="status">
          <span class="lantern" aria-hidden="true"></span>${offline ? "No connection" : state ? "Connected to bot" : "Connecting…"}
        </div>
      </header>

      <div class="side">
        ${offline &&
        html`<p class="offline torn" role="alert">
          No connection to the bot. Check that this phone is on the same Wi-Fi as the laptop.
        </p>`}
        ${playing && html`<${Scores} scores=${state.scores} botRef=${botRef} />`}
        <${Banner} ...${bannerOf(view, state)} />
        ${says && html`<p class="bot-says torn">${says}</p>`}
        ${playing && html`<${Todo} message=${state.message} />`}
        ${playing &&
        html`<${OtherSignals}
          sender=${sender}
          disabled=${offline}
          keepGone=${keepSent || state.keep_destroyed}
          onKeepDestroyed=${() => setKeepSent(true)}
          onEndGame=${() => setEnding(true)}
        />`}
      </div>

      <main class="sheet torn">
        <${Main} key=${view} view=${view} state=${state} sender=${sender} botRef=${botRef} />
      </main>
    </div>

    <${EndGameDialog}
      open=${ending}
      onClose=${() => setEnding(false)}
      onConfirm=${() => sender.send("end-game", null, "End of game sent.")}
    />
    ${toast && html`<div key=${toast.id} class="toast torn" role="status">${toast.text}</div>`}
  `;
}

startForest(document.getElementById("forest"));
render(html`<${App} />`, document.getElementById("root"));
