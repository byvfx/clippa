// War room: pages, keyboard, menus, the look, then start: runs last, once everything above is defined.

// ---------- pages ----------
// One page at a time (body[data-view]; warroom.css shows its .page). The page bar sits under the
// header on a desktop and at the bottom on a phone, where Chat is a page too and More lists the rest.
const PAGES = ["queue", "schedule", "pipeline", "money", "stats", "library", "research", "settings"];
const MORE_PAGES = ["money", "stats", "library", "research", "settings"];  // behind More on a phone
function setView(v) {
  if (!PAGES.includes(v) && !(v === "chat" && PHONE.matches)) v = "queue";  // an old or unknown name
  document.body.dataset.view = v; store.set("warroom.view", v);
  document.querySelectorAll("#views button[data-view]").forEach(b => {
    b.classList.toggle("on", b.dataset.view === v);
    if (b.dataset.view === v) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  $("#pagesMore").classList.toggle("on", MORE_PAGES.includes(v));
  chatSync();  // the Chat view shows the chat (chatdock.js)
  const today = v === "schedule" && PHONE.matches && $(".day.today");
  today ? today.scrollIntoView({ block: "start" }) : scrollTo(0, 0);
}
setView(store.get("warroom.view") || "queue");
for (const k of ["warroom.layout", "warroom.layoutName"]) store.set(k, null);  // the layout editor's, retired 2026-11-18
PHONE.addEventListener("change", () => { if (!PHONE.matches && document.body.dataset.view === "chat") setView("queue"); });
// the page bar sticks under the header, however tall the header wraps, and the docked pane under both
new ResizeObserver(() => {
  document.body.style.setProperty("--head-h", `${$("header").offsetHeight}px`);
  document.body.style.setProperty("--bar-h", `${$("#views").offsetHeight}px`);
}).observe($("header"));

async function playLow(i, full) {  // phones: a 360p copy made by the server (lowres/ beside the preview)
  const tap = $("#mTap");
  if (!tap || (tap.dataset.wait && !full)) return;
  const play = url => {
    const v = document.createElement("video");
    Object.assign(v, { controls: true, playsInline: true, autoplay: true, src: url, poster: i.thumbnail_url || "" });
    tap.replaceWith(v); v.play().catch(() => { /* needs a tap after waiting: the controls are there */ });
  };
  if (full) return play(i.video_url);
  try {
    const r = await api("/api/lowres?src=" + encodeURIComponent(i.video_url));
    if (OPEN !== i.ref || !$("#mTap")) return;
    if (r.ready) return play(r.url);
    tap.dataset.wait = "1";
    tap.querySelector("span").innerHTML = `<b>⏳</b>Making a phone-size copy${r.ahead ? ` (${r.ahead} ahead of it)` : ""}…
      <span class="alt" data-full style="position:static">Play full quality instead (${r.full_mb} MB)</span>`;
    setTimeout(() => { if (OPEN === i.ref && $("#mTap")) { delete $("#mTap").dataset.wait; playLow(i); } }, 8000);
  } catch (e) { toast(e.message, true); }
}

let TOUCH = null;  // swipe left / right in the item window: next / previous
$("#modal").addEventListener("touchstart", e => {
  const t = e.touches[0], v = e.target.closest("video");
  TOUCH = e.touches.length > 1 || e.target.closest("input, textarea") || (v && t.clientY > v.getBoundingClientRect().bottom - 60)
    ? null : { x: t.clientX, y: t.clientY, at: Date.now() };  // not on the video's scrub bar
}, { passive: true });
$("#modal").addEventListener("touchend", e => {
  if (!TOUCH) return;
  const t = e.changedTouches[0], dx = t.clientX - TOUCH.x, dy = t.clientY - TOUCH.y;
  if (Date.now() - TOUCH.at < 700 && Math.abs(dx) > 70 && Math.abs(dx) > 2 * Math.abs(dy)) move(dx < 0 ? 1 : -1);
  TOUCH = null;
}, { passive: true });

// ---------- keyboard ----------

const KEYS = [
  ["Queue", [["J K", "next / previous item (in the item window too)"], ["Enter", "open the item"], ["Esc", "close the window, or clear the selection"]]],
  ["Decide", [["A", "approve (in the item window: then the next item)"], ["R", "reject, asks why"], ["C", "retake note"],
              ["H", "hold / release"], ["P", "pin a release time"], ["U", "reopen an approved item"]]],
  ["Video (item window)", [["Space", "play / pause"], ["← →", "back / forward 5 s"], ["F", "picture in picture"], ["O", "open the video in its own window"],
                           ["B", "box a face on the paused frame: who it is"]]],
  ["Other", [["1 2 3 4 5 6 7", "queue tabs (7: Retakes)"], ["/", "search"], ["L", "watch the live show"], ["M", "show / hide the Clippa chat (Esc in its box comes back)"],
             ["?", "this list"]]],
];
function showKeys() {
  const k = $("#keys");
  k.innerHTML = `<div class="m-head"><b>Keyboard shortcuts</b><button class="x" aria-label="Close" title="Close (Esc)" onclick="this.closest('dialog').close()">×</button></div>
    <div class="k-body">${KEYS.map(([h, rows]) => `<h4>${h}</h4>` + rows.map(([a, b]) =>
      `<span>${a.split(" ").map(x => `<kbd>${esc(x)}</kbd>`).join(" ")}</span><span>${esc(b)}</span>`).join("")).join("")}
    <span></span><span style="color:var(--faint);font-size:12px;margin-top:8px">Off while you type in a box (Esc leaves the box).</span></div>`;
  k.showModal();
}
$("#keysBtn").onclick = showKeys;

function move(d) {
  if (!VISIBLE.length) return;
  const at = VISIBLE.indexOf(OPEN || SEL);
  if (OPEN && at < 0) return;  // opened from the schedule: not in the queue list
  const to = VISIBLE[at < 0 ? 0 : Math.max(0, Math.min(VISIBLE.length - 1, at + d))];
  if (OPEN && to !== OPEN && !discardOk()) return;
  SEL = to;
  renderQueue();
  if (OPEN || dockWanted()) { if (SEL !== OPEN) openModal(SEL, true); }  // docked, J/K opens the pane too
  if (!OPEN || docked()) $(".card.sel")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function keyAct(key, ref) {
  const i = STATE.items.find(x => x.ref === ref);
  if (!i) return;
  if (key === "a") {
    if (i.decision === "approved") return toast("Already approved.");
    if (OPEN && !discardOk()) return;  // approving moves on to the next item
    const at = VISIBLE.indexOf(ref), next = VISIBLE[at + 1] || VISIBLE[at - 1];
    act(ref, "approve");
    if (OPEN) { if (next && next !== ref) { SEL = next; openModal(next, true); } else closeModal(true); }
  } else if (key === "u") { if (i.decision === "approved") act(ref, "reopen"); else toast("Only approved items reopen."); }
  else if (key === "r") { const n = ask(`Reject ${i.short}? Say why (kept as a scoring example):`, true); if (n) act(ref, "reject", { text: n }); }
  else if (key === "c") {
    if (OPEN) { $("#fChanges").focus(); return; }
    const n = ask(`What should change? clipper will re-cut / re-thumbnail it from this note${retakeCost()}:`, true); if (n) act(ref, "changes", { text: n });
  } else if (key === "h") act(ref, "hold", { on: !i.hold });
  else if (key === "p") { if (OPEN !== ref) openModal(ref); $("#fPin").scrollIntoView({ block: "center" }); $("#fPin").focus(); }
}
// #206: the clickable card titles and schedule slots (role="button") answer Enter and Space like a
// button. Caught first, so Enter doesn't also open the selected card and Space doesn't play the video.
document.addEventListener("keydown", e => {
  const t = e.target;
  if ((e.key === "Enter" || e.key === " ") && t.matches?.('[role="button"][tabindex]') && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault(); e.stopImmediatePropagation(); t.click();
  }
}, true);
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey || !STATE) return;
  const tg = e.target;
  if (tg.matches?.("input, textarea, select, [contenteditable]")) { if (e.key === "Escape" && OPEN) { e.preventDefault(); tg.blur(); } return; }
  if ($("#keys").open) { if (e.key === "?") $("#keys").close(); return; }
  if ($("#tool").open) return;
  const take = () => e.preventDefault();
  // the queue's keys act on what is on screen: the Review page (with its docked pane), or an item's window
  // on top of any page; a pane left docked on the Review page doesn't answer from another page
  const q = $("#modal").matches(":modal") || (document.body.dataset.view === "queue" && !tg.closest?.(".menu"));
  // with the pane docked the grid stays usable: a focused button there keeps Space and the arrows
  const grid = docked() && !$("#modal").contains(tg) && !!tg.closest?.("button, a, [role=button], [tabindex]");
  const free = !OPEN || docked();  // the grid's own keys: nothing on top of it
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key, video = OPEN && q && !grid ? $("#modal video") : null;
  if (k === "?") { take(); showKeys(); }
  else if ((k === "j" || k === "k") && q) { take(); move(k === "j" ? 1 : -1); }
  else if (k === "Enter" && free && q && SEL && SEL !== OPEN && !tg.closest?.("button, a")) { take(); openModal(SEL); }
  else if (k === "Escape" && free && LIVEMENU !== null) { take(); LIVEMENU = null; renderLive(); }
  else if (k === "Escape" && OPEN && q && docked()) { take(); closeModal(); }  // on top, the dialog's own cancel
  else if (k === "Escape" && !OPEN) { if (LIVEMENU !== null) { LIVEMENU = null; renderLive(); } else if (SEL) { SEL = null; renderQueue(); } }
  else if ("arhcpu".includes(k) && k.length === 1 && q && (OPEN || SEL)) { take(); keyAct(k, OPEN || SEL); }
  else if (k === " " && video && tg !== video) { take(); video.paused ? video.play() : video.pause(); }
  else if ((k === "ArrowLeft" || k === "ArrowRight") && video && tg !== video) { take(); video.currentTime += k === "ArrowRight" ? 5 : -5; }
  else if (k === "f" && video) {
    take();
    if (document.pictureInPictureElement) document.exitPictureInPicture();
    else if (video.requestPictureInPicture) video.requestPictureInPicture().catch(err => toast(err.message, true));
  }
  else if (k === "o" && q && (OPEN || SEL)) { take(); popOut(OPEN || SEL); }
  else if (k === "m") { take(); toggleChat(); }
  else if (k === "b" && video) { take(); pickFace(OPEN); }
  else if (/^[1-7]$/.test(k) && free && q) { take(); TAB = Object.keys(TABS)[+k - 1]; SEL = null; renderQueue(); }
  else if (k === "/" && free && q) { take(); $("#q").focus(); }
  else if (k === "l") {
    const on = LIVE.filter(l => l.live);
    if (!on.length) return toast("Nothing is live right now.");
    take();
    if (on.length === 1) watch(on[0].channel, store.get("warroom.watch") || "here");
    else { LIVEMENU = LIVE.indexOf(on[0]); renderLive(); }
  }
});

// ---------- menus: the header's ⋯ and, on a phone, the page bar's More ----------
const MENUS = { moreBtn: "moreMenu", pagesMore: "pagesMenu" };
function closeMenus() {
  for (const [b, m] of Object.entries(MENUS)) { $("#" + m).hidden = true; $("#" + b).setAttribute("aria-expanded", "false"); }
}
function openMenu(b) {
  const m = $("#" + MENUS[b]), r = $("#" + b).getBoundingClientRect();
  closeMenus();
  m.hidden = false; $("#" + b).setAttribute("aria-expanded", "true");
  // under the ⋯ button; above the More button (the phone's bar is at the bottom)
  m.style.right = `${Math.max(8, innerWidth - r.right)}px`;
  if (r.top > innerHeight / 2) { m.style.top = "auto"; m.style.bottom = `${innerHeight - r.top + 6}px`; }
  else { m.style.bottom = "auto"; m.style.top = `${r.bottom + 6}px`; }
  m.querySelector("button:not(:disabled)")?.focus();
}
addEventListener("resize", closeMenus);  // placed once, by the button: a menu doesn't follow it
addEventListener("scroll", closeMenus, { passive: true });
document.addEventListener("click", e => {
  const b = e.target.closest?.("#moreBtn, #pagesMore");
  if (b) { $("#" + MENUS[b.id]).hidden ? openMenu(b.id) : closeMenus(); return; }
  const lk = e.target.closest?.("[data-look]");
  if (lk) { ClippaTheme.pick(lk.dataset.look); renderLooks(); }
  if (e.target.closest?.("[data-keys-tile]")) showKeys();
  if (e.target.closest?.("[data-menu-log]")) showLog();
  closeMenus();  // an item was picked (its own handler has run), or a click elsewhere
});
document.addEventListener("keydown", e => {
  const m = e.target.closest?.(".menu");
  if (!m) {
    if (e.key === "Escape" && document.querySelector(".menu:not([hidden])")) { e.preventDefault(); e.stopImmediatePropagation(); closeMenus(); }
    return;
  }
  const items = [...m.querySelectorAll("button:not(:disabled)")], at = items.indexOf(e.target);
  if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closeMenus(); $("#" + Object.keys(MENUS).find(k => MENUS[k] === m.id)).focus(); }
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); e.stopImmediatePropagation();
    items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
  }
}, true);

// ---------- the look: a file in web/themes/ (theme.js); the war room and its chat wear one pick ----------
// Picked on the Settings page (#looks).
let LOOKS = [];
function renderLooks() {
  $("#looks").innerHTML = LOOKS.map(n => `<button class="look ${n === ClippaTheme.current() ? "on" : ""}" data-look="${esc(n)}">${esc(n)}</button>`).join("")
    || `<div class="empty">Can't list them: serve isn't answering.</div>`;
}
async function loadLooks() {
  try { LOOKS = (await api("/api/chat/themes")).themes; ClippaTheme.settle(LOOKS); }   // the default here: <html data-theme>
  catch (e) { /* serve unreachable: the look stays */ }
  renderLooks();
}
loadLooks();

// Everything that polls the server: [refresh, every ms]. Each runs now, then whenever it falls due.
const POLL = [[pollRefresh, 15000], [refreshLive, 60000], [refreshCloud, 60000], [refreshCosts, 300000],
              [refreshUsage, 300000], [refreshStats, 900000]];
loadHash();
// #201: a tab in the background doesn't poll; coming back runs whatever fell due while it was away.
// One check a second rather than a timer each, so a catch-up poll moves the next one along with it
// (with fixed timers the tick after a catch-up came too soon and was skipped: Grok on #211).
const POLLED = new Map();
function pollDue() {
  if (document.hidden) return;
  for (const [fn, every] of POLL) {
    if (Date.now() - (POLLED.get(fn) || 0) < every) continue;
    POLLED.set(fn, Date.now()); fn();
  }
}
pollDue(); setInterval(pollDue, 1000);
document.addEventListener("visibilitychange", pollDue);
