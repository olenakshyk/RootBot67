// Зв'язок із ботом: опитування GET /state і сигнали POST /signals/... .
// Шляхи: docs/requirements/operator-server.md -> HTTP API.

import { useEffect, useState } from "preact/hooks";

const POLL_MS = 1000; // як часто питати стан бота

export async function signal(name, body) {
  let r;
  try {
    r = await fetch(`signals/${name}`, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("No connection to the bot. Check the Wi-Fi and try again.");
  }
  if (!r.ok) throw new Error(await errorText(r));
}

// Текст помилки FastAPI: {"detail": "..."} або {"detail": [{"msg": ...}]} для 422
async function errorText(r) {
  try {
    const { detail } = await r.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  } catch {
    // тіло не JSON
  }
  return `The bot answered HTTP ${r.status}.`;
}

export function useBotState() {
  const [state, setState] = useState(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("state", { cache: "no-store" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const next = await r.json();
        if (alive) {
          setState(next);
          setOffline(false);
        }
      } catch {
        if (alive) setOffline(true);
      }
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return { state, offline };
}
