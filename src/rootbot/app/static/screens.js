// Екрани за станом бота (GET /state) і дії, доступні завжди («Інші сигнали»).
// Тексти кроків (step) задає Orchestrator; тут вони лише перекладаються людською мовою.

import { useEffect, useRef } from "preact/hooks";
import { ArmNote, html, Icon, Medal, ScoreItem, SendButton } from "./ui.js";

// Вузли дерева Orchestrator -> що бот робив
const NODES = {
  ScanBoard: "looking over the board",
  Birdsong: "Birdsong scoring",
  Battles: "a battle",
  Rescan: "re-checking the battle clearings",
  Moves: "moving warriors",
  Recruits: "recruiting warriors",
};
const NODE_PHASE = { ScanBoard: "Birdsong", Birdsong: "Birdsong" };

const STEPS = {
  "birdsong: scanning the board": "Looking over the board",
  "birdsong: scored": "Birdsong scoring",
  daylight: "Reading the order card",
  "daylight: battle": "Battling",
  "daylight: move": "Moving warriors",
  "daylight: recruit": "Recruiting warriors",
  evening: "Wrapping up the turn",
};

export function phaseOf(step) {
  if (step.startsWith("needs help: ")) return NODE_PHASE[step.slice(12)] ?? "Daylight";
  if (step.startsWith("birdsong")) return "Birdsong";
  if (step.startsWith("daylight") || step.startsWith("battle")) return "Daylight";
  if (step.startsWith("evening")) return "Evening";
  return null;
}

export function stepText(step) {
  if (STEPS[step]) return STEPS[step];
  const battle = step.match(/^battle in clearing (\d+)/);
  if (battle) return `Battle in clearing ${battle[1]}`;
  if (step.startsWith("needs help: ")) return `Stuck on ${NODES[step.slice(12)] ?? step.slice(12)}`;
  return step.charAt(0).toUpperCase() + step.slice(1);
}

// --- головні екрани (вміст аркуша) ---

export function Connecting() {
  return html`
    <div class="center">
      <${Icon} name="gear" class="busy-art spin" />
      <h3>Looking for the bot</h3>
      <p class="lead">Open this page on a phone in the same Wi-Fi as the laptop that runs the bot.</p>
    </div>
  `;
}

export function Starting() {
  return html`
    <div class="center">
      <${Icon} name="gear" class="busy-art spin" />
      <h3>The bot is getting ready</h3>
      <p class="lead">It connects to the camera and the arm. The first turn starts when you press Start bot turn.</p>
    </div>
  `;
}

export function PlayersTurn({ sender }) {
  return html`
    <div class="center">
      <h3>Play your turns</h3>
      <p class="lead">When everyone has finished, start the bot's turn. Keep your hands off the board after that.</p>
      <${SendButton}
        class="primary huge"
        label="Start bot turn"
        busyLabel="Turn started"
        inflight=${sender.inflight}
        busy=${sender.busy}
        onClick=${() => !sender.busy && sender.send("your-turn")}
      />
      ${sender.error && html`<p class="send-error" role="alert">${sender.error}</p>`}
    </div>
  `;
}

// Після збою (AUT-7): людина доробляє крок руками й продовжує той самий крок
export function NeedsHelp({ state, sender }) {
  const aborted = state.step === "turn aborted";
  return html`
    <div>
      <h3>${aborted ? "The bot gave up this turn" : "Finish this step by hand"}</h3>
      <p class="lead">
        ${aborted
          ? "The arm went back home and the turn is over. Tidy the board, then start the next turn when it's the bot's go."
          : `The bot couldn't finish ${NODES[state.step.slice(12)] ?? "this step"} and moved the arm away.`}
      </p>
      ${!aborted &&
      html`<ol class="steps">
        <li>Do what the bot was doing: put the pieces where they should be.</li>
        <li>Check that nobody's hands are near the board.</li>
        <li>Continue: the bot picks up right after this step.</li>
      </ol>`}
      ${state.error && html`<p class="error-detail">Error: ${state.error}</p>`}
      <${SendButton}
        class=${aborted ? "primary" : "go"}
        label=${aborted ? "Start bot turn" : "Continue the turn"}
        busyLabel=${aborted ? "Turn started" : "Continuing"}
        inflight=${sender.inflight}
        busy=${sender.busy}
        onClick=${() => !sender.busy && sender.send("your-turn")}
      />
      ${sender.error && html`<p class="send-error" role="alert">${sender.error}</p>`}
    </div>
  `;
}

// «Галявини 1 і 5»
function clearingList(ids) {
  if (ids.length === 1) return `Clearing ${ids[0]}`;
  return `Clearings ${ids.slice(0, -1).join(", ")} and ${ids[ids.length - 1]}`;
}

// Бот розповідає, що нарахував собі на Світанку; за notice.seconds сам переходить до Дня
export function BirdsongNotice({ notice }) {
  const scored = notice.points > 0;
  return html`
    <div class="notice">
      <div class="speaker" aria-hidden="true"><${Icon} name="cat" /></div>
      <div class="bubble torn" role="status">
        <p class="said">
          ${scored
            ? `I looked over the board and scored ${notice.points} VP.`
            : "I looked over the board. Nothing to score this Birdsong."}
        </p>
        <div class="gain">
          <${Medal} points=${scored ? `+${notice.points}` : "0"} />
          <p>
            ${scored
              ? `${clearingList(notice.clearings)}: I rule ${notice.clearings.length > 1 ? "them" : "it"} with enough warriors.`
              : "I don't rule any clearing with enough warriors yet."}
          </p>
        </div>
      </div>
      <div class="next">
        <span>Next: the order card</span>
        <div class="progress"><i style=${`animation-duration:${notice.seconds}s`}></i></div>
      </div>
    </div>
  `;
}

export function BotMoving({ state }) {
  return html`
    <div class="center">
      <${Icon} name="arm" class="busy-art" />
      <h3>${stepText(state.step)}</h3>
      <p class="lead">The bot is taking its turn. It will ask here when it needs you.</p>
      <${ArmNote} />
    </div>
  `;
}

// Кінець гри: переможець (якщо хтось набрав 30 ПО), фінальне табло й «Play again» (сигнал new-game)
export function GameOver({ state, sender }) {
  const entries = Object.entries(state.scores).sort(([, a], [, b]) => b - a);
  const top = entries[0]?.[1];
  const badge = (k, pts) => (state.winner ? (k === state.winner ? "winner" : null) : pts === top ? "most points" : null);
  return html`
    <div>
      <h3>Final scores</h3>
      ${entries.length
        ? html`<ul class="final">
            ${entries.map(
              ([k, pts]) => html`<${ScoreItem} key=${k} faction=${k} points=${pts} badge=${badge(k, pts)} />`,
            )}
          </ul>`
        : html`<p>No scores were recorded.</p>`}

      <h3 class="again-title">Play again</h3>
      <p class="lead">Set up the board for a new game first:</p>
      <ol class="steps">
        <li>Put the Keep in clearing 1 and a Marquise warrior in every clearing except clearing 12.</li>
        <li>Shuffle the deck without dominance cards and with the 4 spy cards, then deal 5 cards face down in a row: that's the bot's Schedule.</li>
        <li>Move every score marker back to 0.</li>
      </ol>
      <${SendButton}
        label="Play again"
        busyLabel="Starting a new game"
        inflight=${sender.inflight}
        busy=${sender.busy}
        onClick=${() => !sender.busy && sender.send("new-game")}
      />
      ${sender.error && html`<p class="send-error" role="alert">${sender.error}</p>`}
    </div>
  `;
}

// --- інші сигнали ---

// keepGone: Keep уже знищено, а відбудувати його не можна, тож кнопки «Keep destroyed» немає
export function OtherSignals({ sender, onEndGame, onKeepDestroyed, keepGone, disabled }) {
  const keepDestroyed = async () => {
    if (disabled) return;
    if (await sender.send("keep-removed", null, "Keep destroyed: the bot forgets it.")) onKeepDestroyed();
  };
  return html`
    <section class="others" aria-labelledby="others-title">
      <h2 id="others-title">Other signals</h2>
      <div class="others-row">
        <button
          type="button"
          class=${keepGone ? "slab quiet torn wide" : "slab quiet torn"}
          aria-disabled=${String(disabled)}
          onClick=${() => !disabled && sender.send("card-given", null, "Card given: the bot scores 1 VP.")}
        >
          Card given to bot
        </button>
        ${!keepGone &&
        html`<button type="button" class="slab quiet torn" aria-disabled=${String(disabled)} onClick=${keepDestroyed}>
          Keep destroyed
        </button>`}
        <button
          type="button"
          class="slab quiet danger torn"
          aria-disabled=${String(disabled)}
          onClick=${() => !disabled && onEndGame()}
        >
          End game
        </button>
      </div>
    </section>
  `;
}

export function EndGameDialog({ open, onClose, onConfirm }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return html`
    <dialog ref=${ref} aria-labelledby="end-title" onClose=${onClose}>
      <form method="dialog" class="confirm torn">
        <h2 id="end-title">End the game?</h2>
        <p>The bot stops playing and shows the final scores. You can't resume this game afterwards.</p>
        <div class="row">
          <button class="slab go torn" value="cancel">Keep playing</button>
          <button class="slab quiet danger torn" value="end" onClick=${onConfirm}>End game</button>
        </div>
      </form>
    </dialog>
  `;
}
