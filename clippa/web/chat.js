// The Clippa messenger (#158): polls /api/chat for new messages, sends what you type, and
// taps cards. The chat lives on serve (clippa/chat.py); this page only draws it. The war room
// shows this same page in a frame at its side (/chat?dock, web/js/chatdock.js): docked, it
// never takes the focus by itself, and it hears from the war room whether it is on screen.
"use strict";

const LOG = document.getElementById("log");
const BOX = document.getElementById("box");
const STATUS = document.getElementById("status");
const SEND = document.getElementById("send");
let LAST = "";            // the newest message id drawn
let AWAY = false;
let BUSY = 0;             // requests waiting on Clippa (typing…)
let UNSEEN = 0;
let DRAWN = false;        // the page's first draw is done (the history: nothing new to count)
const DRAWN_IDS = new Set(); // ids ever drawn, so a full redraw counts only what is new
const TITLE = document.title;
const DOCK = window.parent !== window && new URLSearchParams(location.search).has("dock");
let SEEN = !DOCK;         // docked: whether the war room shows it now (it says so)
if (DOCK) document.documentElement.classList.add("docked");
const unseen = () => document.hidden || !SEEN;
function toDock(msg) { if (DOCK) window.parent.postMessage({ clippa: "dock", ...msg }, location.origin); }

// The link's ?key= has set the cookie by now: keep it out of the address bar.
const URLQ = new URLSearchParams(location.search);
if (URLQ.has("key")) { URLQ.delete("key"); history.replaceState(null, "", location.pathname + (URLQ.toString() ? "?" + URLQ : "")); }

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { signal: AbortSignal.timeout(60000) });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (r.status === 401) throw new Error("signed out: open the war room's ?key= link once more");
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// Plain text with `code` spans and bare links; never HTML from the server.
function rich(text) {
  const frag = document.createDocumentFragment();
  for (const [i, part] of String(text || "").split("`").entries()) {
    if (i % 2) { frag.appendChild(el("code", "", part)); continue; }
    for (const bit of part.split(/(https?:\/\/[^\s)]+)/)) {
      if (/^https?:\/\//.test(bit)) {
        const a = el("a", "", bit); a.href = bit; a.target = "_blank"; a.rel = "noopener"; frag.appendChild(a);
      } else if (bit) frag.appendChild(document.createTextNode(bit));
    }
  }
  return frag;
}

function clock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const STAMP = { done: "done", expired: "expired", cancelled: "no", working: "working…", stale: "changed", failed: "failed" };

function drawCard(m) {
  const c = m.card;
  const box = el("div", `card ${c.state}`);
  box.dataset.card = c.id;
  const head = el("div", "head");
  head.appendChild(el("span", `risk ${c.risk}`, c.risk));
  head.appendChild(el("span", "", c.text));
  box.appendChild(head);
  if (c.lines?.length) {
    const ul = el("ul");
    for (const line of c.lines) ul.appendChild(el("li", "", line));
    box.appendChild(ul);
  }
  if (c.estimate) box.appendChild(el("div", "est", `Estimate: ${c.estimate}`));
  const btns = el("div", "buttons");
  const yes = el("button", "btn", c.risk === "spend" ? "Yes, spend it" : "Yes, do it");
  const no = el("button", "btn", "No");
  yes.type = no.type = "button";
  yes.onclick = () => tap(c.id, true, box);
  no.onclick = () => tap(c.id, false, box);
  btns.append(yes, no);
  box.appendChild(btns);
  box.appendChild(el("span", "stamp", STAMP[c.state] || c.state));
  return box;
}

function setCard(id, state) {
  const box = LOG.querySelector(`[data-card="${CSS.escape(id)}"]`);
  if (!box || box.classList.contains(state)) return;
  box.className = `card ${state}`;
  box.querySelector(".stamp").textContent = STAMP[state] || state;
}

function draw(m) {
  if (m.kind === "divider") {
    LOG.appendChild(el("div", "divider", m.text));
    return;
  }
  const row = el("div", `msg ${m.who}${m.kind === "report" ? " report" : ""}${/failed|cut off/.test(m.kind === "report" ? m.text : "") ? " failed" : ""}`);
  row.dataset.id = m.id;
  if (m.who === "system") {
    row.textContent = m.text;
    LOG.appendChild(row);
    return;
  }
  row.appendChild(el("span", "who", m.who === "me" ? "You" : "Clippa"));
  row.appendChild(el("span", "ts", ` (${clock(m.at)})`));
  row.appendChild(el("span", "sep", ": "));
  if (m.kind === "card") {
    LOG.appendChild(row);
    row.appendChild(drawCard(m));
    return;
  }
  const body = el("span", "body");
  body.appendChild(rich(m.text));
  row.appendChild(body);
  if (m.options?.length && m.ask) {
    const opts = el("div", "options");
    const key = o => o.ref.split("/").pop().split("_")[0];
    const twice = new Set(m.options.map(key).filter((k, i, all) => all.indexOf(k) !== i));
    for (const o of m.options) {   // two s01s: which stream each is from ("Kino Casino 11-14")
      const where = twice.has(key(o)) && o.where ? ` (${o.where})` : "";
      const b = el("button", "btn", `${key(o)}${where} · ${o.title}`);
      b.type = "button";
      b.title = o.ref;
      b.onclick = () => post("/api/chat/pick", { msg: m.id, ref: o.ref });
      opts.appendChild(b);
    }
    row.appendChild(opts);
  }
  LOG.appendChild(row);
}

function apply(v) {
  const atEnd = LOG.scrollHeight - LOG.scrollTop - LOG.clientHeight < 60;
  const first = !DRAWN;
  DRAWN = true;
  if (v.full) { LOG.textContent = ""; LAST = ""; }
  LOG.querySelectorAll(".msg.pending").forEach(e => e.remove());
  let fresh = 0;
  for (const m of v.messages || []) {
    if (LOG.querySelector(`[data-id="${CSS.escape(m.id)}"]`)) continue;
    draw(m);
    if (m.who === "clippa" && !first && !DRAWN_IDS.has(m.id)) fresh++;
    DRAWN_IDS.add(m.id);
  }
  for (const [id, state] of Object.entries(v.cards || {})) setCard(id, state);
  if (v.last) LAST = v.last;
  setAway(!!v.away);
  if (fresh && unseen() && !AWAY) { UNSEEN += fresh; document.title = `(${UNSEEN}) ${TITLE}`; toDock({ unseen: UNSEEN }); }
  if (atEnd || v.full || fresh) LOG.scrollTop = LOG.scrollHeight;
  status(v);
}

function status(v) {
  if (BUSY) { STATUS.textContent = "Clippa is typing…"; STATUS.className = "status typing"; return; }
  STATUS.className = "status";
  const jobs = (v && v.jobs) || [];
  STATUS.textContent = jobs.length ? `working on: ${jobs.map(j => j.tool).join(", ")}`
    : AWAY ? "You're away: reports wait until /back" : "Clippa is online";
}

function setAway(on) {
  AWAY = on;
  document.getElementById("me-state").textContent = on ? "away" : "available";
  document.getElementById("me-dot").className = `dot ${on ? "away" : "online"}`;
  const b = document.getElementById("away");
  b.textContent = on ? "Back" : "Away";
  b.dataset.cmd = on ? "/back" : "/away";
  b.classList.toggle("on", on);
}

async function post(path, body) {
  BUSY++; status();
  try {
    apply(await api(path, body));
  } catch (e) {
    const row = el("div", "msg system", `couldn't reach Clippa: ${e.message}`);
    LOG.appendChild(row); LOG.scrollTop = LOG.scrollHeight;
  } finally {
    BUSY--; status();
  }
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  const echo = el("div", "msg me pending");
  echo.appendChild(el("span", "who", "You"));
  echo.appendChild(el("span", "sep", ": "));
  echo.appendChild(el("span", "body", text));
  LOG.appendChild(echo); LOG.scrollTop = LOG.scrollHeight;
  await post("/api/chat/send", { text });
}

async function tap(id, yes, box) {
  box.querySelectorAll("button").forEach(b => { b.disabled = true; });
  if (yes) setCard(id, "working");
  await post("/api/chat/card", { id, yes });
}

document.getElementById("compose").addEventListener("submit", e => {
  e.preventDefault();
  const t = BOX.value; BOX.value = ""; send(t); BOX.focus();
});
BOX.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); SEND.click(); }
  else if (e.key === "Escape" && DOCK) { e.preventDefault(); toDock({ leave: true }); }   // back to the war room's keys
});
document.querySelectorAll("[data-cmd]").forEach(b => b.addEventListener("click", () => send(b.dataset.cmd)));
document.getElementById("bye").addEventListener("click", () => send("/bye"));
document.getElementById("fold").addEventListener("click", e => {
  const w = document.getElementById("buddies");
  w.classList.toggle("folded");
  e.currentTarget.setAttribute("aria-expanded", String(!w.classList.contains("folded")));
});

let POLL_T = 0, POLLING = false;
async function poll() {   // also called to look now (back on screen): one loop either way
  clearTimeout(POLL_T);
  if (POLLING) return;
  POLLING = true;
  try {
    apply(await api(`/api/chat?since=${encodeURIComponent(LAST)}`));
  } catch (e) {
    if (!BUSY) { STATUS.className = "status"; STATUS.textContent = `can't reach Clippa (${e.message})`; }
  } finally {
    POLLING = false;
    POLL_T = setTimeout(poll, unseen() ? 15000 : 3000);
  }
}

let BUDDIES_T = 0, BUDDIES_BUSY = false;
async function buddies() {   // also called when the dock comes on screen: one loop either way
  clearTimeout(BUDDIES_T);
  if (BUDDIES_BUSY) return;
  if (unseen()) { BUDDIES_T = setTimeout(buddies, 30000); return; }   // nobody is looking: the next round
  BUDDIES_BUSY = true;
  try {
    const v = await api("/api/chat/buddies");
    const ul = document.getElementById("crew");
    ul.textContent = "";
    let up = 0;
    for (const b of v.buddies) {
      const li = el("li", `buddy ${b.state}`);
      li.appendChild(el("span", "dot"));
      li.appendChild(el("span", "name", b.name));
      li.title = `${b.name}: ${b.state}${b.note ? ", " + b.note : ""}`;
      if (b.note) li.appendChild(el("span", "note", b.note));
      if (b.state !== "offline" && b.state !== "unknown") up++;
      ul.appendChild(li);
    }
    document.getElementById("crew-count").textContent = `(${up}/${v.buddies.length})`;
  } catch (e) { /* the chat's own status line says when serve is unreachable */ }
  BUDDIES_BUSY = false;
  BUDDIES_T = setTimeout(buddies, 30000);
}

// ---------- the look: web/themes/, kept by theme.js (ClippaTheme); no theme is special here ----------
const PICK = document.getElementById("theme-pick");

// A theme names the windows with --label-<name> (its data, not code here).
function labels() {
  const css = getComputedStyle(document.documentElement);
  document.querySelectorAll("[data-label]").forEach(e => {
    const v = css.getPropertyValue(`--label-${e.dataset.label}`).trim().replace(/^(["'])(.*)\1$/, "$2");
    if (v) e.textContent = v;
  });
  if (PICK.querySelector(`option[value="${CSS.escape(ClippaTheme.current())}"]`)) PICK.value = ClippaTheme.current();
}
async function themes() {
  try {
    const v = await api("/api/chat/themes");
    PICK.textContent = "";
    for (const t of v.themes) { const o = el("option", "", t); o.value = t; PICK.appendChild(o); }
    ClippaTheme.settle(v.themes, v.default);   // [chat] theme, unless this browser picked one
  } catch (e) { /* serve unreachable: the look stays */ }
  labels();
}
document.addEventListener("clippa-theme", labels);
PICK.addEventListener("change", () => ClippaTheme.pick(PICK.value));

function seenNow() { UNSEEN = 0; document.title = TITLE; toDock({ unseen: 0 }); }
document.addEventListener("visibilitychange", () => { if (!unseen()) { seenNow(); poll(); } });
// The war room says when it shows or hides the dock, and asks for the box (its M key).
addEventListener("message", e => {
  if (!DOCK || e.origin !== location.origin || e.source !== window.parent || e.data?.clippa !== "dock") return;
  if ("seen" in e.data) {
    SEEN = !!e.data.seen;
    if (!unseen()) { seenNow(); LOG.scrollTop = LOG.scrollHeight; poll(); buddies(); }
  }
  if (e.data.focus) BOX.focus();
});
if (window.matchMedia("(max-width: 720px)").matches) document.getElementById("buddies").classList.add("folded");
themes();
poll();
buddies();
if (!DOCK) BOX.focus();   // docked, the war room's keys stay the war room's
toDock({ hello: true });   // docked: the war room answers with whether it shows the dock
