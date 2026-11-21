// War room: state, helpers, the overview refresh, live shows, search and the review queue.

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// One of the page's line icons (the sprite at the top of warroom.html), drawn in the text's colour.
const ic = name => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const KIND = { pick: "Clip", short: "Short", segment: "Segment", compilation: "Comp" };
const DEC = { pending: "waiting", changes_requested: "changes", approved: "approved", rejected: "rejected" };
let STATE = null, TAB = "waiting", SHOW = "", KINDF = "", Q = "", BUSY = new Set(), OPEN = null;
let ST = "", FROM = "", TO = "", SORT = "", THITS = {}, THQ = "", PICK = new Set(), LASTPICK = null;
let VISIBLE = [], SEL = null, SEL_AT = 0;  // the queue in display order; the keyboard's item
const PHONE = matchMedia("(max-width: 700px)");

function mmss(s) { if (s == null) return ""; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function ago(iso) { if (!iso) return ""; const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return "just now"; if (s < 5400) return Math.round(s / 60) + " min ago"; if (s < 129600) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " d ago"; }
function usd(x) { return x == null ? "?" : x < 0.1 ? `$${(+x).toFixed(3)}` : `$${(+x).toFixed(2)}`; }
// #203: before an action that makes model calls, what it should cost (from past ones) and a yes
function retakeCost() {  // " (a retake, about $0.020)" when a note starts one here
  const rc = STATE?.retake_cost;
  return rc?.auto ? ` (starts a retake${rc.avg_usd != null ? `, about ${usd(rc.avg_usd)}` : ""})` : "";
}
function retakeOk(n) {
  const rc = STATE?.retake_cost, a = rc?.avg_usd;
  if (!rc?.auto) return true;  // [retake] auto off: nothing runs from here
  return confirm(`This starts ${n} retake${n > 1 ? "s" : ""} (a model call each)${a != null ? `, about ${usd(a * n)}` : ""}. Go?`);
}
function toast(msg, err, undoId) { const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  $("#announce").textContent = msg;  // a live region that is always there, so screen readers say it
  if (undoId) { const b = document.createElement("button"); b.className = "btn"; b.textContent = "Undo"; b.onclick = () => { t.remove(); undo(undoId); }; t.appendChild(b); }
  else if (err && /serve log/.test(msg)) { const b = document.createElement("button"); b.className = "btn"; b.textContent = "Log"; b.onclick = () => { t.remove(); showLog({ which: "serve", q: "", bad: true }); }; t.appendChild(b); }
  document.querySelectorAll(".toast").forEach(o => o.remove());
  document.body.appendChild(t); setTimeout(() => t.remove(), undoId ? 10000 : err ? 7000 : 3500); }
async function api(path, body) {
  // A read gives up after 2 min, so a hung one can't hold the timer's refresh (pollRefresh) for good.
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { signal: AbortSignal.timeout(120000) });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (r.status === 401) throw new Error("signed out: open the war room's ?key= link again");  // #206: the cookie went
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

// The health light in the header: serve, the bot, the Kitsu sync and how old the numbers are.
function healthLight(age) {
  const h = STATE?.health, svc = h?.services || {}, why = [];
  for (const k of ["serve", "bot"]) if (svc[k] && svc[k].level !== "ok") why.push(`${svc[k].label}: ${svc[k].text}`);
  if (h?.sync?.error) why.push("Kitsu sync: " + h.sync.error);
  const down = why.length > 0;  // a service or the sync: red; a failed job: amber
  const failed = Object.entries(svc.serve?.last || {}).filter(([, j]) => j.exit).map(([k, j]) => `${k} exit ${j.exit}`);
  if (failed.length) why.push("failed: " + failed.join(", "));
  const level = DOWN || age > 120 || down ? "bad" : age === null || age > 45 || failed.length ? "mid" : "ok";
  return { level, why };
}
function tick() {
  // #201: the light says how old the numbers are, whatever the last poll did
  const age = STATE ? Math.max(0, (Date.now() - new Date(STATE.generated_at)) / 1000) : null, w = $("#wHealth");
  const was = age === null ? "" : age < 90 ? `${Math.round(age)} s ago` : ago(STATE.generated_at);
  const { level, why } = healthLight(age);
  w.dataset.level = level;
  w.querySelector("b").textContent = DOWN ? (DOWN.signedOut ? "Signed out" : "Offline") : level === "ok" ? "OK" : age === null ? "…" : "Check";
  $("#updated").textContent = age === null ? "" : age < 90 ? `${Math.round(age)} s` : was.replace(" ago", "");
  const title = [DOWN ? (DOWN.signedOut ? "Signed out: open the war room's ?key= link again." : `serve isn't answering: ${DOWN.msg}\nTrying again every 15 s.`) : "",
    ...why, age === null ? "No data yet" : `Updated ${ago(STATE.generated_at)}`, "Opens the Pipeline page (Health)"].filter(Boolean).join("\n");
  if (w.title !== title) w.title = title;  // not every second while it is hovered: the age in it is to the minute
  fitHeader();  // "OK" to "Offline" can tip the live shows onto their own row
}
setInterval(tick, 1000);

// #201: a failed refresh says so once and in the header, not in a toast every 15 s; before the
// first answer the panels say why they are empty (until then they show the page's own skeletons).
let DOWN = null;  // {msg} while the overview isn't coming back
const FIRST = ["#streams", "#health", "#grid", "#week", "#schedule", "#uploads"];
function waiting(msg) {
  if (STATE || !msg) return;
  for (const s of FIRST) $(s).innerHTML = `<div class="empty">Can't load: ${esc(msg)}.<br>Trying again every 15 s.</div>`;
}
let DRAWN = false;  // render() has filled the panels once
let MUTATED = 0;  // bumped when an action's answer changes STATE.items here
let REFRESHING = 0;  // overviews asked for and not answered yet
// The timer's refresh waits for one still on its way: a slow serve (a cold cache over the NAS
// share takes a minute) got another request every 15 s on top. An action's refresh always goes.
function pollRefresh() { if (!REFRESHING) refresh(); }
async function refresh() {
  REFRESHING++;
  try {
    const asked = MUTATED, s = await api("/api/overview");
    if (DOWN) { if (!DOWN.signedOut) toast("serve is answering again"); DOWN = null; }
    // #202: answers aren't ordered, and one asked for before an action landed would undo it for a
    // moment; the refresh each action starts afterwards brings the real state
    if (asked !== MUTATED || STATE && new Date(s.generated_at) < new Date(STATE.generated_at)) return;
    STATE = s; render();
  }
  catch (e) {
    if (!DOWN) toast("Refresh failed: " + e.message, true);
    DOWN = { msg: e.message, signedOut: e.message.startsWith("signed out") }; waiting(e.message); tick();
  }
  finally { REFRESHING--; }
}
// ---------- live shows ----------

let LIVE = [], LIVEMENU = null;
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private window */ } } };
// Kick gives "2026-10-23 01:02:03" in UTC, with no zone: as epoch ms
const kickMs = iso => +new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso.replace(" ", "T") + "Z");
function since(iso) {
  if (!iso) return "";
  const t = kickMs(iso);
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  return isNaN(m) ? "" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}
function recording(l) {  // the NAS recorder's stream for this channel, if it's on it now
  return (STATE?.streams || []).find(s => s.channel === l.channel && ["recording", "paused", "finalizing"].includes(s.recording));
}
async function refreshLive() {
  try { LIVE = await api("/api/live"); renderLive(); }
  catch (e) { /* Kick unreachable: keep the last state */ }
}
function renderLive() {
  // keep the keyboard's place (and the menu's tick) across a re-render, the 60 s poll's included
  const ae = document.activeElement, had = ae?.closest?.("#lives .live")?.dataset.live;
  const inMenu = ae?.id === "liveRemember" ? "#liveRemember" : ae?.dataset?.watch ? `[data-watch="${ae.dataset.watch}"]` : "[data-watch]";
  const ticked = $("#liveRemember")?.checked;
  $("#lives").innerHTML = LIVE.map((l, n) => {
    const rec = l.live ? recording(l) : null;
    const tip = l.live ? `${l.title || ""}\nlive ${since(l.since)} · ${rec ? "recording " + rec.id : "NOT recording"}` : `${l.channel}: offline, click for the channel`;
    // #206: the chip's label is a real button (keyboard, screen readers); its menu sits beside it
    return `<span class="live ${l.live ? "on" : ""}" data-live="${n}" title="${esc(tip)}"><button class="chipb" type="button"><span class="dot"></span>${esc(l.show_name)}${l.live
      ? ` · LIVE${l.viewers != null ? " · " + l.viewers.toLocaleString() : ""}${l.since ? " · " + since(l.since) : ""} <span class="rec ${rec ? "" : "off"}">${rec ? `<i class="recdot"></i>REC` : "no rec"}</span>` : ""}</button>${LIVEMENU === n ? liveMenu(l, rec) : ""}</span>`;
  }).join("");
  if (ticked && $("#liveRemember")) $("#liveRemember").checked = true;
  if (had != null) $(`#lives .live[data-live="${had}"] ${LIVEMENU === +had ? inMenu : ".chipb"}`)?.focus();
  fitHeader();
}
// The live shows sit in the header's row while they fit beside the rest, else on a row of their own
// (header.stack). Measured from the chips' own widths, which don't depend on the row, so it can't flip
// back and forth; 24 px of slack before they come back up, for the widgets' text changing. With no chips
// (before /api/live answers) a narrow window still stacks, so the rest can wrap rather than run off the edge.
function fitHeader() {
  const h = $("header"), chips = [...$("#lives").children];
  if (PHONE.matches) { h.classList.remove("stack"); return; }  // phones: a row of their own anyway
  const gap = parseFloat(getComputedStyle(h).columnGap) || 0;
  const sum = els => els.reduce((s, e) => s + e.getBoundingClientRect().width, 0);  // unrounded, unlike offsetWidth
  const rest = [...h.children].filter(e => e.id !== "lives" && e.offsetWidth && !["absolute", "fixed"].includes(getComputedStyle(e).position));  // not an open ⋯ menu
  const need = sum(rest) + gap * rest.length + (chips.length ? sum(chips) + 8 * (chips.length - 1) : 0);  // 8: the gap between chips
  const room = h.clientWidth - parseFloat(getComputedStyle(h).paddingLeft) - parseFloat(getComputedStyle(h).paddingRight);
  if (need > room) h.classList.add("stack");
  else if (need + 24 <= room) h.classList.remove("stack");
}
function liveMenu(l, rec) {
  return `<div class="livemenu" data-menu>
    <div class="info">${esc(l.channel)} · ${rec ? `recording ${esc(rec.id)} (${esc(rec.recording)})` : "the NAS isn't recording it"}</div>
    <button class="btn" data-watch="here" data-ch="${esc(l.channel)}">${ic("play")}Watch here</button>
    <button class="btn" data-watch="window" data-ch="${esc(l.channel)}">${ic("external")}Open in a new window</button>
    <label><input type="checkbox" id="liveRemember"> Always do this (shift-click a chip to ask again)</label></div>`;
}
function watch(ch, where) {
  const name = LIVE.find(l => l.channel === ch)?.show_name || ch;
  if (where === "window") { window.open(`https://kick.com/${encodeURIComponent(ch)}`, `kick-${ch}`); return; }
  const d = $("#dock"); d.hidden = false;
  d.innerHTML = `<div class="d-head"><span class="dot" style="width:8px;height:8px;border-radius:50%;background:var(--red)"></span><b>${esc(name)}</b><span style="color:var(--faint)">${esc(ch)}</span>
    <span class="sp"></span><button class="btn" id="dSize" title="Bigger / smaller" aria-label="Bigger or smaller">${ic("expand")}</button>
    <button class="btn" id="dPop" title="Open on kick.com in a new window instead" aria-label="Open on kick.com">${ic("external")}</button>
    <button class="btn" id="dClose" title="Close the player" aria-label="Close the player">${ic("close")}</button></div>
    <iframe src="https://player.kick.com/${encodeURIComponent(ch)}?autoplay=true" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>`;
  d.classList.toggle("big", !!store.get("warroom.dock.big"));
  $("#dSize").onclick = () => { d.classList.toggle("big"); store.set("warroom.dock.big", d.classList.contains("big") ? "1" : null); };
  $("#dPop").onclick = () => { closeDock(); watch(ch, "window"); };
  $("#dClose").onclick = closeDock;
}
function closeDock() { const d = $("#dock"); d.innerHTML = ""; d.hidden = true; }
function liveClick(e, t) {
  if (e.target.closest("[data-menu]")) {
    const w = e.target.closest("[data-watch]");
    if (!w) return;
    if ($("#liveRemember")?.checked) store.set("warroom.watch", w.dataset.watch);
    LIVEMENU = null; renderLive(); watch(w.dataset.ch, w.dataset.watch);
    return;
  }
  const n = +t.dataset.live, l = LIVE[n];
  if (!l.live) { window.open(`https://kick.com/${encodeURIComponent(l.channel)}`, "_blank"); return; }
  const remembered = store.get("warroom.watch");
  if (remembered && !e.shiftKey) { watch(l.channel, remembered); return; }
  if (e.shiftKey) store.set("warroom.watch", null);  // shift-click: forget the choice, ask
  LIVEMENU = LIVEMENU === n ? null : n; renderLive();
}

function render() {
  const s = STATE, c = s.counts;
  renderWidgets();
  const first = !DRAWN; DRAWN = true;
  renderStreams(); renderHealth(); renderQueue(); renderUploads(); renderSchedule(); renderLive(); tick();
  if (first && document.body.dataset.view === "schedule") setView("schedule");  // opened on it: go to today
  if (OPEN) { const r = s.items.find(i => i.ref === OPEN); if (r && !$("#modal").contains(document.activeElement)) fillModalState(r); }
}

// The header's widgets (each opens its page). Spend is filled by refreshCosts (panels.js).
function renderWidgets() {
  const u = STATE.uploads, c = STATE.counts, wu = $("#wUploads");
  if (u) {
    const pct = u.max_per_24h ? Math.min(100, 100 * u.last_24h / u.max_per_24h) : 0;
    wu.querySelector("b").textContent = `${u.last_24h}/${u.max_per_24h}`;
    Object.assign(wu.querySelector(".meter i").style, { width: `${pct}%` });
    wu.querySelector(".meter i").className = u.room ? "" : "full";
    wu.title = `Uploads in the last 24 hours: ${u.last_24h} of ${u.max_per_24h}${u.room ? `, ${u.room} more now` : ", at the cap"}. ${c.scheduled_7d} planned in 7 days.`;
  }
  // the recorder's states: recording (the show is live), paused (offline, waiting out the grace period),
  // finalizing (ended, closing the file); Grok on #314: only the first is live
  const st = STATE.streams, n = f => st.filter(f).length;
  const live = n(x => x.recording === "recording"), ending = n(x => ["paused", "finalizing"].includes(x.recording));
  const run = n(x => x.run_status === "running"), bad = n(x => x.run_status === "failed");
  const wp = $("#wPipe");
  wp.querySelector("b").textContent = [live && `${live} live`, ending && `${ending} ending`, run && `${run} running`,
    bad && `${bad} failed`].filter(Boolean).join(" · ") || "idle";
  wp.dataset.level = bad ? "bad" : live || ending || run ? "busy" : "";
  wp.title = `${live} live, ${ending} paused or finalizing, ${run} processing, ${bad} failed. Opens the Pipeline page.`;
  $("#vnSched").textContent = c.approved_unscheduled || "";
}

function renderStreams() {
  const st = STATE.streams;
  $("#pipeSub").textContent = st.filter(x => x.run_status === "running").length ? "running" : "";
  $("#streams").innerHTML = st.slice(0, 10).map(x => {
    const done = STATE.stages.filter(g => x.stages[g] === "done").length;
    const status = x.recording && x.recording !== "done" ? x.recording : x.run_status === "running" ? "running"
      : x.run_status === "failed" ? "failed" : done === STATE.stages.length ? "done" : x.published ? "in review" : `${done}/${STATE.stages.length}`;
    return `<div class="stream" title="${esc(x.run_error)}">
      <div class="top">${showTag(x.show, x.show_name || x.channel)}<b style="font-size:12px">${esc(x.started_at.slice(0, 10))}</b>
        <span style="margin-left:auto;font-size:11px;color:${x.run_status === "failed" ? "var(--red)" : x.run_status === "running" ? "var(--accent)" : "var(--faint)"}">${x.recording && x.recording !== "done" ? `<i class="recdot"></i>` : ""}${esc(status)}</span></div>
      <div class="t">${esc(x.title)}</div>
      <div class="stages" role="img" aria-label="${esc(STATE.stages.map(g => `${g} ${x.stages[g] || "not yet"}`).join(", "))}">${STATE.stages.map(g => `<div class="stage ${esc(x.stages[g] || "")}" title="${g}: ${x.stages[g] || "not yet"}"></div>`).join("")}</div>
      <div class="meta"><span>${esc(x.id)}</span>${x.faces ? `<button class="lnk" data-faces="${esc(x.id)}" title="Name this stream's face groups">${ic("face")}faces</button>` : ""}${x.stages.signals === "done" ? `<button class="lnk" data-heat="${esc(x.id)}" title="Where this stream ran hot, and what was cut from it">${ic("heat")}heat</button>` : ""}<span>${x.duration_s ? (x.duration_s / 3600).toFixed(1) + " h" : ""}</span></div></div>`;
  }).join("") || `<div class="empty">No streams yet</div>`;
}

// Retakes is a queue tab: the items with a retake, newest first, each with its last note and result.
let RETAKEN = new Map();  // ref -> its newest retake (STATE.retakes comes newest first)
function retaken() { RETAKEN = new Map(); for (const h of STATE.retakes || []) { const r = `${h.stream}/${h.key}`; if (!RETAKEN.has(r)) RETAKEN.set(r, h); } }
function retakeResult(h) {
  return `<div class="r-${esc(h.result)}">${h.result === "applied" ? ic("check") + esc(h.summary || (h.applied || []).join("; "))
    : h.result === "unsupported" ? ic("warn") + esc(h.unsupported || "not applied") : h.result === "error" ? ic("close") + esc(h.error) : esc(h.result)}</div>`;
}
function retakeSub() {  // #203: what retakes cost
  const rc = STATE.retake_cost || {};
  return STATE.health.retake_running ? "a retake is working…" : rc.month_n ? `${rc.month_n} retakes this month · ${usd(rc.month_usd)}` : "";
}

// A show's colour (the look's --show-1 to --show-6, by its place in [shows]: the server's hue) and a
// title without the show's own "SHOW NAME | " prefix, which the tag already says.
function showTag(key, name) {
  const n = (STATE?.shows || []).find(s => s.key === key || (!key && s.name === name))?.hue || 0;
  return name ? `<span class="show${n ? ` s${n}` : ""}">${esc(name)}</span>` : "";
}
function bare(title, show) {
  const m = /^(.+?)\s*\|\s*(.+)$/.exec(title || "");
  return m && show && m[1].trim().toLowerCase() === show.trim().toLowerCase() ? m[2] : title || "";
}

function renderHealth() {
  const h = STATE.health, svc = h.services || {};
  // serve and the bot from their heartbeats (streams/.serve, .bot: any machine), with what serve is running
  const lines = ["serve", "bot"].filter(k => svc[k]).map(k => {
    const s = svc[k], busy = s.running && s.running.length ? ` · ${s.running.join(", ")} running` : "";
    return `<div>${esc(s.label)}: <span class="${s.level}">${esc(s.text)}</span>${esc(busy)}</div>`;
  }).join("");
  const last = svc.serve && svc.serve.last ? Object.entries(svc.serve.last)
    .filter(([, j]) => j.exit).map(([k, j]) => `${k} exit ${j.exit} ${ago(j.at)}`) : [];
  $("#health").innerHTML = lines + `
    ${last.length ? `<div class="bad" style="font-size:12px">failed: ${esc(last.join("; "))}</div>` : ""}
    <div>Retakes: <span class="${h.retake_running ? "mid" : "ok"}">${h.retake_running ? "working" : "idle"}</span></div>
    <div>Kitsu sync: <span class="${h.sync.error ? "bad" : h.sync.running ? "mid" : "ok"}">${h.sync.running ? "syncing…" : h.sync.error ? esc(h.sync.error) : h.sync.last ? ago(h.sync.last) : "via serve"}</span></div>
    <div>YouTube uploads: <span class="${h.auto_upload_minutes ? "ok" : "mid"}">${h.auto_upload_minutes ? "automatic, every " + h.auto_upload_minutes + " min" : "by hand (clipper upload)"}</span></div>
    <div>Schedule: ${STATE.schedule_generated_at ? "planned " + ago(STATE.schedule_generated_at) : "never planned"}</div>`;
  renderBusy();
}

// #24: one search box over every stream's titles, descriptions, topics, hashtags and, from
// the server, the items' captions; filters combine, and live in the URL (#q=...&st=...).
const STATUS = { unslotted: i => i.decision === "approved" && !i.scheduled && !i.uploaded && !i.dropped,
  scheduled: i => !!i.scheduled && !i.uploaded, uploaded: i => i.uploaded, held: i => i.hold,
  pinned: i => !!i.publish_at, dropped: i => !!i.dropped, issues: i => (i.issues || []).length > 0 };
const STATUS_LABEL = { unslotted: "Approved, no slot", scheduled: "Scheduled", uploaded: "Uploaded", held: "Held",
  pinned: "Pinned", dropped: "Not going out (stale / over budget)", issues: "Has style issues or check findings" };
function textOf(i) {
  return [i.title, i.hook, i.ref, i.description, i.topic, i.hashtags, i.playlist, i.category, i.thumb_text]
    .join(" ").toLowerCase();
}
function filtered() {
  let items = STATE.items;
  if (SHOW) items = items.filter(i => i.show === SHOW);
  if (KINDF) items = items.filter(i => i.kind === KINDF);
  if (ST && STATUS[ST]) items = items.filter(STATUS[ST]);
  if (FROM) items = items.filter(i => (i.date || "") >= FROM);
  if (TO) items = items.filter(i => (i.date || "") <= TO);
  if (Q.trim()) {
    const ws = Q.toLowerCase().split(/\s+/).filter(Boolean);
    items = items.filter(i => { const t = textOf(i); return ws.every(w => t.includes(w)) || (THQ === Q && THITS[i.ref]); });
  }
  return items;
}
let THT = null;
function searchTranscripts() {
  clearTimeout(THT);
  if (Q.trim().length < 3) { THITS = {}; THQ = ""; return; }
  THT = setTimeout(async () => {
    const q = Q;
    $("#queueSub").textContent = "searching transcripts…";
    try { const r = await api("/api/search?q=" + encodeURIComponent(q)); if (q === Q) { THITS = r.hits || {}; THQ = q; renderQueue(); } }
    catch (e) { toast(e.message, true); }
  }, 700);
}
function saveHash() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries({ tab: TAB === "waiting" ? "" : TAB, show: SHOW, kind: KINDF, q: Q, st: ST, from: FROM, to: TO, sort: SORT })) if (v) p.set(k, v);
  const was = new URLSearchParams(location.hash.slice(1));  // an investigation link (investigate.js) stays
  for (const k of ["inv", "run", "lead"]) if (was.get(k)) p.set(k, was.get(k));
  const h = p.toString();
  if (h !== location.hash.slice(1)) history.replaceState(null, "", location.pathname + location.search + (h ? "#" + h : ""));
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  TAB = TABS[p.get("tab")] ? p.get("tab") : "waiting"; SHOW = p.get("show") || ""; KINDF = p.get("kind") || "";
  Q = p.get("q") || ""; ST = p.get("st") || ""; FROM = p.get("from") || ""; TO = p.get("to") || "";
  SORT = Object.hasOwn(SORTS, p.get("sort") || "") ? p.get("sort") || "" : "";
  if (Q) searchTranscripts();
}
window.addEventListener("hashchange", () => { loadHash(); if (STATE) renderQueue(); });
// The link's ?key= has set the cookie by now: keep it out of the address bar, screenshots and copied links.
const URLQ = new URLSearchParams(location.search);
if (URLQ.has("key")) { URLQ.delete("key"); history.replaceState(null, "", location.pathname + (URLQ.toString() ? "?" + URLQ : "") + location.hash); }
const TABS = { waiting: i => i.decision === "pending", changes: i => i.decision === "changes_requested",
  approved: i => i.decision === "approved" && !i.uploaded, live: i => i.uploaded, rejected: i => i.decision === "rejected", all: () => true,
  retakes: i => RETAKEN.has(i.ref) };
// #206: the queue's order. Newest stream first is the default; ties keep the ref order.
const newest = (a, b) => (b.date || "").localeCompare(a.date || "") || a.ref.localeCompare(b.ref);
const num = x => { const n = parseFloat(x); return isNaN(n) ? -Infinity : n; };
const SORTS = { "": ["Newest stream", newest], old: ["Oldest stream", (a, b) => -newest(a, b)],
  score: ["Score, high first", (a, b) => num(b.score) - num(a.score) || newest(a, b)],
  long: ["Longest", (a, b) => (b.duration_s || 0) - (a.duration_s || 0) || newest(a, b)],
  short: ["Shortest", (a, b) => (a.duration_s || Infinity) - (b.duration_s || Infinity) || newest(a, b)],  // no length: last
  kind: ["Kind", (a, b) => Object.keys(KIND).indexOf(a.kind) - Object.keys(KIND).indexOf(b.kind) || newest(a, b)] };
const TAB_LABEL = { waiting: "Waiting", changes: "Changes", approved: "Approved", live: "Uploaded", rejected: "Rejected", all: "All", retakes: "Retakes" };
// Show, kind, status, order and dates sit behind one Filters button; what is on shows as chips.
let FOPEN = false;
function filterChips() {
  const show = STATE.shows.find(s => s.key === SHOW);
  return [[SHOW, "show", show ? show.name : SHOW], [KINDF, "kind", (KIND[KINDF] || KINDF) + "s"], [ST, "st", STATUS_LABEL[ST] || ST],
    [SORT, "sort", SORTS[SORT]?.[0]], [FROM, "from", "from " + FROM], [TO, "to", "to " + TO]].filter(([v]) => v);
}

function renderQueue() {
  retaken();
  const base = filtered();
  const shows = STATE.shows;
  // B12: the 15 s refresh rebuilt this bar under the cursor: the search box, selects and date
  // pickers lost focus mid-typing. While focus is in it, only the tabs' counts change.
  const bar = $("#queueBar");
  if (bar.firstChild && bar.contains(document.activeElement)) {
    bar.querySelectorAll("[data-tab]").forEach(t => {
      t.classList.toggle("on", TAB === t.dataset.tab);
      t.querySelector(".n").textContent = base.filter(TABS[t.dataset.tab]).length;
    });
  } else {
    const chips = filterChips();
    bar.innerHTML = `<span class="tabs">${Object.keys(TABS).map(t => `<button class="tab ${TAB === t ? "on" : ""}" data-tab="${t}">${TAB_LABEL[t]}<span class="n">${base.filter(TABS[t]).length}</span></button>`).join("")}</span>`
    + `<span class="sep"></span><button class="btn ${chips.length ? "on" : ""}" id="filterBtn" aria-expanded="${FOPEN}" aria-controls="filters">Filters${chips.length ? ` <b>${chips.length}</b>` : ""}</button>
       ${chips.map(([, k, label]) => `<button class="fchip" data-unfilter="${k}" title="Clear this filter">${esc(label)}${ic("close")}</button>`).join("")}
       <input class="search" id="q" aria-label="Search the queue" placeholder="Search titles, descriptions, transcripts… ( / )" value="${esc(Q)}">
       <button class="btn" id="pickAll" title="Select every item shown, for a bulk action">${ic("selectall")}Select shown</button>
       <div class="filters" id="filters" ${FOPEN ? "" : "hidden"}><div class="fhead"><b>Filters</b><button class="btn" id="filterDone">Done</button></div><select id="showSel" aria-label="Show"><option value="">All shows</option>${shows.map(s => `<option value="${s.key}" ${SHOW === s.key ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
       <select id="kindSel"><option value="">All kinds</option>${Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${KINDF === k ? "selected" : ""}>${v}s</option>`).join("")}</select>
       <select id="stSel"><option value="">Any status</option>${Object.keys(STATUS).map(k => `<option value="${k}" ${ST === k ? "selected" : ""}>${STATUS_LABEL[k]}</option>`).join("")}</select>
       <select id="sortSel" aria-label="Sort the queue" title="Order">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}" ${SORT === k ? "selected" : ""}>${l}</option>`).join("")}</select>
       <label class="dl">From <input type="date" class="date" id="fromD" value="${esc(FROM)}" title="Streams from this day"></label>
       <label class="dl">To <input type="date" class="date" id="toD" value="${esc(TO)}" title="...to this day"></label>
       ${chips.length ? `<button class="btn" data-unfilter="all">Clear all</button>` : ""}</div>`;
  }
  const byRetake = (a, b) => (RETAKEN.get(b.ref)?.at || "").localeCompare(RETAKEN.get(a.ref)?.at || "");
  const items = base.filter(TABS[TAB]).sort(TAB === "retakes" && !SORT ? byRetake : SORTS[SORT][1]);
  VISIBLE = items.map(i => i.ref);
  if (SEL && !VISIBLE.includes(SEL)) SEL = VISIBLE[Math.min(SEL_AT, VISIBLE.length - 1)] || null;  // acted on: the next one
  SEL_AT = SEL ? VISIBLE.indexOf(SEL) : 0;
  // a retake at work shows on every tab, as the Retakes panel did; the month's cost on the Retakes tab
  const rs = TAB === "retakes" || STATE.health.retake_running ? retakeSub() : "";
  $("#queueSub").textContent = `${items.length} shown` + (rs ? " · " + rs : "")
    + (Q.trim().length >= 3 && THQ !== Q ? " · searching transcripts…" : "");
  renderBulk(); saveHash();
  $("#vnQueue").textContent = STATE.counts.pending || "";
  $("#grid").innerHTML = items.map(card).join("") || emptyQueue(base);
}
function emptyQueue(base) {  // all caught up: say what goes out next rather than show an empty grid
  if (TAB === "retakes") return `<div class="empty">No retakes yet. Ask for changes with a note and Clippa re-cuts the item.</div>`;
  if (TAB !== "waiting" || base.length !== STATE.items.length) return `<div class="empty">Nothing here with these filters.</div>`;
  const nx = STATE.uploads?.next?.[0], show = nx && STATE.items.find(i => i.ref === nx.key)?.show_name;
  return `<div class="empty caught"><b>Nothing waiting.</b>${nx ? `<span>Next upload ${esc(new Date(nx.publish_at).toLocaleString([], { timeZone: STATE.timezone, weekday: "short", hour: "2-digit", minute: "2-digit" }))}: ${esc(bare(nx.title, show))}</span>` : ""}</div>`;
}

function card(i) {
  const vertical = i.kind === "short";
  const media = i.thumbnail_url ? `<img loading="lazy" src="${i.thumbnail_url}" alt="">`
    : i.video_url ? `<video muted preload="${PHONE.matches ? "none" : "metadata"}" src="${i.video_url}#t=3"></video>` : "";
  const sched = i.scheduled ? `${ic("clock")}${new Date(i.scheduled).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : i.publish_at ? `${ic("pin")}${esc(i.publish_at)}` : "";
  const hit = THQ === Q && THITS[i.ref] && !textOf(i).includes(Q.toLowerCase().split(/\s+/)[0]) ? THITS[i.ref] : "";
  return `<div class="card ${BUSY.has(i.ref) ? "busy" : ""} ${SEL === i.ref ? "sel" : ""} ${PICK.has(i.ref) ? "picked" : ""}" data-ref="${esc(i.ref)}">
    <div class="thumb ${vertical ? "vertical" : ""}" data-open="${esc(i.ref)}">${media}
      <span class="kind ${esc(i.kind)}">${esc(KIND[i.kind] || i.kind)}</span>
      <span class="state ${esc(i.decision)}">${esc(DEC[i.decision] || i.decision)}${i.hold ? " · held" : ""}</span>
      <span class="dur">${mmss(i.duration_s)}</span>${i.video_url ? `<button class="pop" data-pop="${esc(i.ref)}" title="Open the video in its own window" aria-label="Pop out">${ic("popout")}</button>` : ""}</div>
    <div class="body">
      <div class="title" data-open="${esc(i.ref)}" role="button" tabindex="0" title="${esc(i.title)}">${esc(bare(i.title, i.show_name))}</div>
      <div class="sub">${showTag(i.show, i.show_name)}<span>${esc(i.date)}</span><span>${esc(i.short.split("/")[1])}</span>${i.score ? `<span class="score" title="Score (Filters: sort by score)">${esc(i.score)}</span>` : ""}${sched ? `<span>${sched}</span>` : ""}${i.issues?.length ? `<span class="issues" title="${esc(i.issues.map(x => x.msg).join("\n"))}">${ic("warn")}${i.issues.length}</span>` : ""}</div>
      ${i.hook ? `<div class="hook">${esc(i.hook)}</div>` : ""}
      ${i.why ? `<div class="why" title="${esc(i.why)}">${esc(i.why)}</div>` : ""}
      ${hit ? `<div class="hit" title="Said in the video">${ic("said")}${esc(hit)}</div>` : ""}
      ${i.decision === "changes_requested" && i.comment ? `<div class="note">${ic("edit")}${esc(i.comment)}</div>` : ""}
      ${TAB === "retakes" && RETAKEN.has(i.ref) ? (h => `<div class="ev"><div class="w">${esc(ago(h.at))}${h.cost_usd != null ? " · " + usd(h.cost_usd) : ""}</div><q>${esc(h.comment)}</q>${retakeResult(h)}</div>`)(RETAKEN.get(i.ref)) : ""}
    </div>
    <div class="acts">
      <label class="pickl" title="Select (shift-click: a range)"><input type="checkbox" aria-label="Select" data-pick="${esc(i.ref)}" ${PICK.has(i.ref) ? "checked" : ""}></label>
      ${i.decision !== "approved" ? `<button class="btn good" data-act="approve" data-ref="${esc(i.ref)}" title="Approve (A)">${ic("check")}Approve</button>` : `<button class="btn" data-act="reopen" data-ref="${esc(i.ref)}" title="Reopen (U)">${ic("reopen")}Reopen</button>`}
      <button class="btn warn" data-act="changes" data-ref="${esc(i.ref)}" title="A retake note (C)">${ic("edit")}Changes</button>
      <button class="btn bad" data-act="reject" data-ref="${esc(i.ref)}" title="Reject (R)" aria-label="Reject">${ic("close")}</button>
    </div></div>`;
}
