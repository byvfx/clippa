// Ctrl+K (war room redesign step 5): one box that reaches every page, an item by its title, the ⋯ actions,
// the queue tabs, the looks and the Library, Research and Settings windows. ↑ ↓ pick, Enter runs, Esc
// closes. The G keys (app.js) jump straight to a page: G then the page's letter below.

const PAGE_NAME = { queue: "Review", schedule: "Schedule", pipeline: "Pipeline", money: "Money", stats: "Stats",
  library: "Library", research: "Research", settings: "Settings" };
const PAGE_ICON = { queue: "review", schedule: "schedule", pipeline: "pipeline", money: "money", stats: "stats",
  library: "library", research: "search", settings: "settings" };
const GO_KEYS = { r: "queue", s: "schedule", p: "pipeline", m: "money", t: "stats", l: "library", f: "research", ",": "settings" };
const GO_KEY = Object.fromEntries(Object.entries(GO_KEYS).map(([k, v]) => [v, k]));
const PAL_ITEMS = 8;   // items shown for a search; the queue's own search finds the rest
const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

// The palette's and the G keys' way to a page: an item window on top of the page closes first (it asks
// about unsaved edits), and a window from a tile does too, so the page is what you see.
function goPage(v) {
  if ($("#modal").matches(":modal")) { closeModal(); if ($("#modal").open) return; }
  for (const d of ["#keys", "#tool"]) if ($(d).open) $(d).close();
  closeMenus();
  setView(v);
}

// How well `q` matches `text`: null when it doesn't (every word of q must be in it), else lower is
// better: the text starts with the first word, a word of it does, or the word is inside one.
function palScore(q, text) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean), t = String(text || "").toLowerCase();
  if (!words.length) return 0;
  if (!words.every(w => t.includes(w))) return null;
  if (t.startsWith(words[0])) return 0;
  return t.split(/[^\p{L}\p{N}]+/u).some(x => x.startsWith(words[0])) ? 1 : 2;
}

function palEntries() {
  const pages = PAGES.map(v => ({ label: PAGE_NAME[v], icon: PAGE_ICON[v], hint: `G ${GO_KEY[v].toUpperCase()}`, run: () => goPage(v) }));
  const tabs = Object.keys(TABS).map((t, k) => ({ label: `${TAB_LABEL[t]} tab`, sub: "Review", icon: "review", hint: String(k + 1),
    run: () => { goPage("queue"); if (document.body.dataset.view !== "queue") return; TAB = t; SEL = null; if (STATE) renderQueue(); } }));
  const click = sel => () => $(sel).click();
  const live = LIVE.filter(l => l.live);
  const actions = [
    { label: "Sync Kitsu", icon: "sync", run: click("#syncBtn") },
    { label: "Replan the schedule", icon: "schedule", run: click("#replanBtn") },
    { label: "Undo a war room action…", icon: "undo", run: showUndo },
    { label: "The serve and bot log", icon: "pipeline", run: () => showLog() },
    { label: "Cast", sub: "Library", icon: "library", run: click("#castBtn") },
    { label: "News", sub: "Research", icon: "search", run: click("#newsBtn") },
    { label: "Blinds", sub: "Research", icon: "search", run: click("#blindsBtn") },
    { label: "Investigations", sub: "Research", icon: "search", run: click("#invBtn") },
    { label: "API keys", sub: "Settings", icon: "settings", run: click("#apikeysBtn") },
    { label: "Keyboard shortcuts", icon: "keys", hint: "?", run: showKeys },
    { label: "Show or hide the Clippa chat", icon: "chat", hint: "M", run: toggleChat },
    ...live.map(l => ({ label: `Watch ${l.show_name} live`, icon: "play", hint: "L", run: () => watch(l.channel, store.get("warroom.watch") || "here") })),
    ...LOOKS.filter(n => n !== ClippaTheme.current()).map(n => ({ label: `Look: ${n}`, sub: "Settings", icon: "settings",
      run: () => { ClippaTheme.pick(n); renderLooks(); } })),
  ];
  return [["Pages", pages], ["Actions", actions], ["Queue tabs", tabs]];
}

let PAL = { groups: [], list: [], at: 0 };
function showPalette() {
  const d = $("#palette");
  if (d.open) { $("#palQ").select(); return; }
  closeMenus();   // an open ⋯ would take the palette's Esc
  d.innerHTML = `<div class="pal-box">${ic("search")}<input id="palQ" role="combobox" aria-expanded="true" aria-controls="palList"
      aria-autocomplete="list" aria-label="Go to a page, find an item, run an action" autocomplete="off" spellcheck="false"
      placeholder="Go to a page, find an item, run an action"><kbd>Esc</kbd></div>
    <div class="pal-list" id="palList" role="listbox" aria-label="Results"></div>`;
  PAL.groups = palEntries();
  d.showModal();
  palFill();
  $("#palQ").focus();
}
function palFill() {
  const q = $("#palQ").value.trim();
  const ranked = rows => rows.map(r => [palScore(q, `${r.label} ${r.sub || ""}`), r]).filter(([s]) => s !== null)
    .sort((a, b) => a[0] - b[0]).map(([, r]) => r);
  const groups = PAL.groups.map(([name, rows]) => [name, ranked(rows)]);
  if (q && STATE) {   // items by title, show, kind or name ("s07"): the best few
    const items = STATE.items.map(i => [palScore(q, `${bare(i.title, i.show_name)} ${i.short} ${i.show_name || ""} ${KIND[i.kind] || ""}`), i])
      .filter(([s]) => s !== null).sort((a, b) => a[0] - b[0]).slice(0, PAL_ITEMS)
      .map(([, i]) => ({ label: bare(i.title, i.show_name) || i.short, sub: `${KIND[i.kind] || i.kind} · ${DEC[i.decision] || i.decision}`,
        icon: i.kind === "short" ? "play" : "image", run: () => openModal(i.ref) }));
    groups.splice(1, 0, ["Items", items]);   // after the pages: a search is mostly for an item
  }
  PAL.list = groups.flatMap(([, rows]) => rows);
  PAL.at = 0;
  let n = 0;
  $("#palList").innerHTML = groups.filter(([, rows]) => rows.length).map(([name, rows], g) =>
    `<div role="group" aria-labelledby="palg${g}"><div class="pal-h" id="palg${g}">${esc(name)}</div>${rows.map(r => {
      const k = n++;
      return `<div class="pal-o" role="option" id="palo${k}" data-pal="${k}" aria-selected="${k === 0}">${ic(r.icon)}<span class="t">${esc(r.label)}</span>${r.sub ? `<span class="sub">${esc(r.sub)}</span>` : ""}${r.hint ? r.hint.split(" ").map(x => `<kbd>${esc(x)}</kbd>`).join("") : ""}</div>`;
    }).join("")}</div>`).join("") || `<div class="empty">Nothing matches “${esc(q)}”</div>`;
  palMark(0);
}
function palMark(k, scroll) {
  if (!PAL.list.length) { $("#palQ").removeAttribute("aria-activedescendant"); return; }
  PAL.at = (k + PAL.list.length) % PAL.list.length;
  for (const o of document.querySelectorAll("#palList .pal-o")) o.setAttribute("aria-selected", String(+o.dataset.pal === PAL.at));
  $("#palQ").setAttribute("aria-activedescendant", `palo${PAL.at}`);
  if (scroll) $(`#palo${PAL.at}`)?.scrollIntoView({ block: "nearest" });
}
function palRun(k) {
  const r = PAL.list[k];
  if (!r) return;
  $("#palette").close();
  for (const d of ["#keys", "#tool"]) if ($(d).open) $(d).close();   // what it opens comes up alone
  r.run();
}

document.addEventListener("keydown", e => {   // anywhere on the page, a box included; Cmd+K on a Mac
  if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") { e.preventDefault(); showPalette(); }
});
$("#palette").addEventListener("input", palFill);
$("#palette").addEventListener("keydown", e => {
  if (e.target.id !== "palQ") return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); palMark(PAL.at + (e.key === "ArrowDown" ? 1 : -1), true); }
  else if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); palRun(PAL.at); }
  if (e.key === "Escape") e.stopPropagation();   // the dialog's own cancel: the page's keys would take it for the item
});
$("#palette").addEventListener("mousemove", e => { const o = e.target.closest(".pal-o"); if (o && +o.dataset.pal !== PAL.at) palMark(+o.dataset.pal); });
$("#palette").addEventListener("click", e => {
  const o = e.target.closest(".pal-o");
  if (o) palRun(+o.dataset.pal);
  else if (e.target === $("#palette")) $("#palette").close();   // the backdrop
});
$("#palBtn").onclick = showPalette;
document.addEventListener("click", e => { if (e.target.closest?.("[data-palette]")) showPalette(); });   // the phone's More menu
$("#palBtn kbd").textContent = MAC ? "Cmd K" : "Ctrl K";
