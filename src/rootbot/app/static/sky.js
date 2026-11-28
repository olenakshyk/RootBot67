// Небо за порою доби в грі: Світанок — ранок, День — день, Вечір — вечір, хід людей — ніч.
// Кольори перетікають у CSS ([data-time] на <html>), сонце й місяць ідуть дугою (--theta на .celestial).

// Кут сонця на дузі: 0 — зеніт, від'ємний — схід (ліворуч), додатний — захід; 180 — ніч, угорі місяць
const THETA = { morning: -65, day: 0, evening: 65, night: 180 };
const MAX_FORWARD = 240; // далі вперед, ніж на стільки градусів, — краще трохи назад

let theta = null;

export function setSkyTime(time) {
  const root = document.documentElement;
  const celestial = document.getElementById("celestial");
  root.dataset.time = time;

  const target = THETA[time];
  if (theta === null) {
    // перше відкриття сторінки: без проходу по дузі
    theta = target;
    celestial.style.setProperty("--theta", `${theta}deg`);
    requestAnimationFrame(() => requestAnimationFrame(() => celestial.classList.remove("instant")));
    return;
  }
  // день іде вперед (за годинниковою стрілкою): сонце сідає праворуч, місяць сходить зліва
  const forward = (((target - theta) % 360) + 360) % 360;
  theta += forward <= MAX_FORWARD ? forward : forward - 360;
  celestial.style.setProperty("--theta", `${theta}deg`);
}
