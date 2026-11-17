// War room: a stream's heat graph. The curve `clipper signals` makes from chat and loudness,
// the windows picked from it, and where each cut item sits in the stream, in its review colour.

let HEAT = null;  // {focus, data, geo}: the open window
let HEATWIN = 0;
// state -> [what the legend and the tips call it, its colour]; the word is always shown too
const HEAT_STATE = {
  cut: ["not in review", "var(--faint)"], waiting: ["waiting", "var(--text)"], changes: ["changes asked", "var(--amber)"],
  approved: ["approved", "var(--green)"], scheduled: ["scheduled", "var(--blue)"], uploaded: ["uploaded", "var(--violet)"],
  rejected: ["rejected", "var(--red)"],
};
const HEAT_LANES = [["segment", "Segments"], ["short", "Shorts"], ["pick", "Clips"]];

function hm(t) { return `${Math.floor(t / 3600)}:${String(Math.floor(t % 3600 / 60)).padStart(2, "0")}`; }
function heatTop(r) {  // the y axis' top: a round number over the hottest second
  const max = Math.max(1, ...r.heat.filter(v => v != null));
  return max <= 5 ? Math.ceil(max) : Math.ceil(max / 5) * 5;
}
function heatWhat(i) { return `${KIND[i.kind] || i.kind} · ${HEAT_STATE[i.state][0]}${i.hold ? " · held" : ""}${i.dropped ? " · no slot: " + i.dropped : ""}`; }

// The graph, W px wide: the plot on top, a lane per kind of item under it, the time axis last.
function heatSvg(r, W, focus) {
  const L = 62, R = 10, T = 8, PH = 150, LH = 18, n = r.duration_s || 1, top = heatTop(r), b = r.bucket_s;
  const lanes = HEAT_LANES.filter(([k]) => r.items.some(i => i.kind === k));
  const H = T + PH + 8 + lanes.length * LH + 20;
  const x = t => L + (W - L - R) * Math.min(n, Math.max(0, t)) / n, y = v => T + PH * (1 - Math.min(top, v) / top);
  HEAT.geo = { L, R, W, n };
  const f = v => v.toFixed(1);
  let area = "", line = "", gaps = "", run = null, gap = null;
  const endRun = k => {
    if (run === null) return;
    area += ` L${f(x(k * b))},${y(0)} Z`; run = null;
  };
  r.heat.forEach((v, k) => {
    if (v == null) {
      endRun(k);
      if (gap === null) gap = k;
      return;
    }
    if (gap !== null) { gaps += `<rect class="gap" x="${f(x(gap * b))}" y="${T}" width="${f(x(k * b) - x(gap * b))}" height="${PH}"/>`; gap = null; }
    const px = f(x((k + .5) * b)), py = f(y(v));
    if (run === null) { run = k; area += `M${f(x(k * b))},${y(0)} L${px},${py}`; line += `M${px},${py}`; }
    else { area += ` L${px},${py}`; line += ` L${px},${py}`; }
  });
  endRun(r.heat.length);
  if (gap !== null) gaps += `<rect class="gap" x="${f(x(gap * b))}" y="${T}" width="${f(x(n) - x(gap * b))}" height="${PH}"/>`;
  const yt = [0, top / 2, top].map(v => `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  const step = [300, 600, 900, 1800, 3600, 7200, 14400].find(s => n / s <= (W - L - R) / 64) || 28800;
  let xt = "";
  for (let t = 0; t <= n; t += step) xt += `<line class="gl" x1="${f(x(t))}" x2="${f(x(t))}" y1="${T}" y2="${T + PH}"/><text x="${f(x(t))}" y="${H - 5}" text-anchor="middle">${hm(t)}</text>`;
  const thr = r.min_heat != null && r.min_heat < top ? `<line class="thr" x1="${L}" x2="${W - R}" y1="${y(r.min_heat)}" y2="${y(r.min_heat)}"/>` : "";
  const wins = r.windows.map(w => `<rect class="win" x="${f(x(w.start_s))}" y="${T}" width="${f(Math.max(1, x(w.end_s) - x(w.start_s)))}" height="${PH}"/>`).join("");
  const dots = r.windows.map(w => `<circle class="pk" cx="${f(x(w.peak_s))}" cy="${f(y(w.heat))}" r="${w.rank <= 10 ? 3.2 : 2.2}"/>`).join("");
  const fi = focus && r.items.find(i => i.ref === focus);
  const fband = fi ? `<rect class="fband" x="${f(x(fi.start_s) - 1)}" y="${T}" width="${f(Math.max(3, x(fi.end_s) - x(fi.start_s)) + 2)}" height="${H - T - 20}"/>` : "";
  const rows = lanes.map(([kind, label], k) => {
    const ly = T + PH + 8 + k * LH;
    return `<text class="ln" x="4" y="${ly + 12}">${label}</text>` + r.items.filter(i => i.kind === kind).map(i =>
      `<rect class="it${i.ref === focus ? " on" : ""}${i.ref ? " go" : ""}" x="${f(x(i.start_s))}" y="${ly + 2}" width="${f(Math.max(3, x(i.end_s) - x(i.start_s)))}" height="${LH - 5}" rx="2"
        style="fill:${HEAT_STATE[i.state][1]}"${i.ref ? ` data-heatref="${esc(i.ref)}" tabindex="0" role="button"` : ""} aria-label="${esc(`${i.title}, ${heatWhat(i)}, ${mmss(i.start_s)} to ${mmss(i.end_s)}`)}"/>`).join("");
  }).join("");
  return `<svg id="heatSvg" width="${W}" height="${H}" role="img" aria-label="Heat over the stream, with what was cut. The table below lists the hottest moments.">
    ${gaps}${wins}${yt}${xt}${thr}${fband}<path class="area" d="${area}"/><path class="curve" d="${line}"/>${dots}${rows}
    <line id="heatCur" class="cur" y1="${T}" y2="${H - 20}" visibility="hidden"/></svg>`;
}

function heatOver(r, a, z) { return r.items.filter(i => i.start_s < z && i.end_s > a); }
function heatItemBtn(i) {
  const c = `<span class="sw" style="background:${HEAT_STATE[i.state][1]}"></span>`;
  return i.ref ? `<button class="lnk" data-heatref="${esc(i.ref)}" title="${esc(heatWhat(i))}">${c}${esc(i.title)}</button>` : `<span title="${esc(heatWhat(i))}">${c}${esc(i.title)}</span>`;
}
function heatBody(r) {
  if (r.error) return `<div class="empty">${esc(r.error)}</div>`;
  if (!r.heat.length) return `<div class="empty">No heat for this stream yet: it is made by <code>clipper signals ${esc(r.stream)}</code>.</div>`;
  const hot = r.windows.reduce((m, w) => !m || w.heat > m.heat ? w : m, null);
  const kinds = HEAT_LANES.map(([k, label]) => [r.items.filter(i => i.kind === k).length, label.toLowerCase()]).filter(c => c[0]);
  const used = Object.keys(HEAT_STATE).map(s => [s, r.items.filter(i => i.state === s).length]).filter(c => c[1]);
  const legend = used.map(([s, c]) => `<span><span class="sw" style="background:${HEAT_STATE[s][1]}"></span>${HEAT_STATE[s][0]} ${c}</span>`).join("")
    + `<span><span class="sw win"></span>a window clipper looked at</span>${r.min_heat != null ? `<span><span class="sw thr"></span>the least heat a window needs (${esc(r.min_heat)})</span>` : ""}`
    + (r.heat.includes(null) ? `<span><span class="sw gap"></span>no chat recorded</span>` : "");
  const said = r.windows.some(w => w.said);  // signals ran before the transcript: no words to show
  const table = r.windows.slice().sort((a, z) => a.rank - z.rank).slice(0, 12).map(w => {
    const cut = heatOver(r, w.start_s, w.end_s);
    return `<tr><td>${esc(w.rank)}</td><td>${mmss(w.peak_s)}</td><td>${w.heat.toFixed(1)}</td>${said ? `<td class="said">${w.said ? `“${esc(w.said)}”` : ""}</td>` : ""}
      <td>${cut.map(heatItemBtn).join("<br>") || `<span class="none">nothing cut here</span>`}</td></tr>`;
  }).join("");
  return `<div class="hsum">${mmss(r.duration_s)} long${hot ? ` · hottest at ${mmss(hot.peak_s)} (${hot.heat.toFixed(1)})` : ""} · ${r.windows.length} windows${kinds.length ? " · " + kinds.map(c => c.join(" ")).join(", ") : " · nothing cut yet"}${
      r.chat_coverage != null && r.chat_coverage < .995 ? ` · chat covers ${Math.round(r.chat_coverage * 100)}% of it` : ""}</div>
    <div class="hwrap" id="heatWrap"></div>
    <div class="hleg">${legend}</div>
    ${table ? `<table class="htab"><thead><tr><th>#</th><th>At</th><th>Heat</th>${said ? `<th class="said">Said at the peak</th>` : ""}<th>Cut from it</th></tr></thead><tbody>${table}</tbody></table>` : ""}
    <div class="note">Heat is chat speed, laughing and "clip it" in chat and how loud the room is, each against the last few minutes (<code>[signals]</code>). The graph keeps the hottest second of every ${r.bucket_s} s.</div>
    <div class="tip" id="heatTip" hidden></div>`;
}
function heatDraw() {  // the graph at the window's width; a long stream scrolls sideways on a phone
  const box = $("#heatWrap");
  if (!box || !HEAT?.data) return;
  const width = () => Math.max(320, box.clientWidth, Math.round(HEAT.data.duration_s / 3600 * 70));
  let W = width();
  box.innerHTML = heatSvg(HEAT.data, W, HEAT.focus);
  if (width() !== W) { W = width(); box.innerHTML = heatSvg(HEAT.data, W, HEAT.focus); }  // the graph's own height brought a scrollbar
  const on = box.querySelector(".it.on");
  if (on && W > box.clientWidth) box.scrollLeft = Math.max(0, on.x.baseVal.value - box.clientWidth / 2);
}

async function showHeat(stream, focus) {
  const d = $("#tool"), win = ++HEATWIN;
  d.dataset.kind = "heat"; d.classList.remove("cover"); d.classList.add("facesw");
  const list = (STATE?.streams || []).filter(s => s.stages.signals === "done" || s.id === stream);
  if (!list.some(s => s.id === stream)) list.unshift({ id: stream, show_name: "", started_at: "", title: stream });
  const opts = list.map(s => `<option value="${esc(s.id)}"${s.id === stream ? " selected" : ""}>${esc([s.show_name || s.channel, s.started_at.slice(0, 10), s.title].filter(Boolean).join(" · ").slice(0, 90))}</option>`).join("");
  d.innerHTML = `<div class="m-head"><b>Heat</b><select id="heatStream" title="The stream">${opts}</select><button class="x" aria-label="Close" title="Close (Esc)" data-close>×</button></div><div class="tbody heat">Loading…</div>`;
  if (!d.open) d.showModal();
  HEAT = { focus: focus || "", data: null, geo: null };
  try {
    const r = await api("/api/heat?stream=" + encodeURIComponent(stream));
    if (win !== HEATWIN || !d.open || d.dataset.kind !== "heat") return;
    HEAT.data = r;
    d.querySelector(".tbody").innerHTML = heatBody(r);
    heatDraw();
  } catch (e) {
    if (win === HEATWIN && d.dataset.kind === "heat") d.querySelector(".tbody").innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  }
}

// One tip for the whole graph: the time under the pointer, its heat, the window and the items there.
function heatTip(e) {
  const svg = $("#heatSvg"), tip = $("#heatTip"), cur = $("#heatCur");
  if (!svg || !tip || !HEAT?.data || !HEAT.geo) return;
  // the wrapper's box as well as the graph's: a long graph scrolled sideways is wider than what shows
  const box = svg.getBoundingClientRect(), seen = svg.parentNode.getBoundingClientRect(), g = HEAT.geo, r = HEAT.data, px = e.clientX - box.left;
  if (e.clientY < box.top || e.clientY > box.bottom || e.clientX < seen.left || e.clientX > seen.right || px < g.L || px > g.W - g.R) { tip.hidden = true; cur.setAttribute("visibility", "hidden"); return; }
  const t = (px - g.L) / (g.W - g.L - g.R) * g.n, v = r.heat[Math.min(r.heat.length - 1, Math.floor(t / r.bucket_s))];
  const w = r.windows.filter(w => w.start_s <= t && t <= w.end_s).sort((a, z) => a.rank - z.rank)[0];
  const z = v => v == null ? "?" : esc(v);  // a window whose file has no score for it
  const under = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-heatref]")?.dataset.heatref;
  const its = heatOver(r, t, t).sort((a, z) => (z.ref === under) - (a.ref === under)).slice(0, 4);
  cur.setAttribute("x1", px); cur.setAttribute("x2", px); cur.setAttribute("visibility", "visible");
  tip.innerHTML = `<b>${mmss(t)}</b> · ${v == null ? "no chat recorded" : "heat " + v.toFixed(1)}`
    + (w ? `<br>window #${esc(w.rank)}: chat ${z(w.chat_z)} · laughs ${z(w.react_z)} · “clip” ${z(w.clip_z)} · loud ${z(w.loud_z)}${w.said ? `<br><i>“${esc(w.said.slice(0, 90))}”</i>` : ""}` : "")
    + its.map(i => `<br><span class="sw" style="background:${HEAT_STATE[i.state][1]}"></span>${esc(i.title.slice(0, 60))} <span class="dimmer">${esc(heatWhat(i))}</span>`).join("");
  tip.hidden = false;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  tip.style.left = Math.max(6, Math.min(innerWidth - tw - 6, e.clientX + 14)) + "px";
  tip.style.top = Math.max(6, e.clientY + 16 + th > innerHeight ? e.clientY - th - 12 : e.clientY + 16) + "px";
}
document.addEventListener("pointermove", heatTip);
document.addEventListener("pointerdown", heatTip);

function heatOpen(ref) {  // an item on the graph or in the table: its window in the queue
  if (!STATE?.items.some(i => i.ref === ref)) { toast("That item isn't on the page yet: it will be after the next refresh"); return; }
  if (OPEN === ref) { $("#tool").close(); return; }  // its window is under this one, as you left it
  if (OPEN && !discardOk()) return;
  $("#tool").close();
  openModal(ref);
}
document.addEventListener("click", e => {
  const go = e.target.closest?.("[data-heatref]"), b = e.target.closest?.("[data-heat]");
  if (go) heatOpen(go.dataset.heatref);
  else if (b) showHeat(b.dataset.heat, b.dataset.heatfocus || "");
});
// Before app.js's Enter/Space handler (this file loads first): that one calls click(), which an SVG shape doesn't have.
document.addEventListener("keydown", e => {
  const go = (e.key === "Enter" || e.key === " ") && e.target.closest?.("svg [data-heatref]");
  if (go) { e.preventDefault(); e.stopImmediatePropagation(); heatOpen(go.dataset.heatref); }
}, true);
document.addEventListener("change", e => { if (e.target.id === "heatStream") showHeat(e.target.value); });
let HEATRZ = 0;
window.addEventListener("resize", () => { clearTimeout(HEATRZ); HEATRZ = setTimeout(() => { if ($("#tool").open && $("#tool").dataset.kind === "heat") heatDraw(); }, 150); });
$("#tool").addEventListener("close", () => { if ($("#tool").dataset.kind === "heat") { HEAT = null; HEATWIN++; } });
