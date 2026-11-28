// Сосни на тлі: три ряди, намальовані в пікселях під розмір вікна (перемальовуються при зміні розміру),
// тож на широкому екрані їх більше, а не вони розтягнуті й обрізані.

const SEED = 42;

export function startForest(svg) {
  let frame = 0;
  const draw = () => drawForest(svg);
  draw();
  addEventListener("resize", () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(draw);
  });
}

function drawForest(svg) {
  const W = Math.ceil(svg.clientWidth);
  const H = Math.ceil(svg.clientHeight);
  if (!W || !H) return;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  let seed = SEED;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const f = (n) => n.toFixed(1);

  // Одна сосна: яруси обвислих гілок з рваним нижнім краєм; верхні яруси малюються останніми.
  const pine = (cx, base, h, w, color) => {
    const top = base - h;
    const crownBottom = base - h * 0.07;
    const tiers = 5 + Math.floor(rnd() * 2);
    const step = (crownBottom - top) / tiers;
    const lean = (rnd() - 0.5) * w * 0.12;
    let out = `<rect x="${f(cx - w * 0.05)}" y="${f(crownBottom - step)}" width="${f(w * 0.1)}" height="${f(base - crownBottom + step + 2)}" fill="${color}"/>`;
    for (let i = tiers - 1; i >= 0; i--) {
      const bot = top + step * (i + 1);
      const tTop = i === 0 ? top : bot - step * 2.1;
      const hw = w * (0.28 + 0.72 * ((i + 1) / tiers)) * 0.5;
      const apex = cx + lean * (1 - i / tiers);
      const droop = step * 0.32;
      let d = `M${f(apex)} ${f(tTop)}`;
      d += ` Q${f(cx + hw * 0.3)} ${f(tTop + (bot - tTop) * 0.55)} ${f(cx + hw)} ${f(bot + droop * 0.4)}`;
      const teeth = 3 + Math.floor(rnd() * 2);
      for (let k = 1; k <= teeth * 2; k++) {
        const x = cx + hw - (2 * hw * k) / (teeth * 2);
        const y = k % 2 ? bot - droop * (0.6 + rnd() * 0.5) : bot + droop * (0.1 + rnd() * 0.4);
        d += ` L${f(x)} ${f(y)}`;
      }
      d += ` Q${f(cx - hw * 0.3)} ${f(tTop + (bot - tTop) * 0.55)} ${f(apex)} ${f(tTop)}Z`;
      out += `<path d="${d}" fill="${color}"/>`;
    }
    return out;
  };

  const row = (base, hMin, hMax, wRatio, gapMin, gapMax, color) => {
    let out = "";
    for (let x = -30 + rnd() * 20; x < W + 40; x += gapMin + rnd() * (gapMax - gapMin)) {
      const h = Math.min(hMin + rnd() * (hMax - hMin), H * 0.95);
      out += pine(x, base, h, h * wRatio * (0.85 + rnd() * 0.3), color);
    }
    return out;
  };

  let out = `<defs><linearGradient id="fog" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="var(--sky-low)" stop-opacity="0"/>
    <stop offset="1" stop-color="var(--sky-low)" stop-opacity=".75"/></linearGradient></defs>`;
  out += `<g opacity=".55">${row(H - 70, 90, 150, 0.42, 22, 38, "var(--pine-far)")}</g>`;
  out += `<rect x="0" y="${H - 75}" width="${W}" height="75" fill="var(--pine-far)" opacity=".55"/>`;
  out += `<rect x="0" y="${H - 170}" width="${W}" height="170" fill="url(#fog)"/>`;
  out += row(H - 34, 120, 190, 0.5, 40, 70, "var(--pine-mid)");
  out += `<rect x="0" y="${H - 40}" width="${W}" height="40" fill="var(--pine-mid)"/>`;
  out += row(H + 6, 170, 250, 0.54, 90, 160, "var(--pine-near)");
  out += `<rect x="0" y="${H - 8}" width="${W}" height="8" fill="var(--pine-near)"/>`;
  svg.innerHTML = out;
}
