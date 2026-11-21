// War room: blind items (#111, `clippa blinds`). Gossip blinds with their clues read, your guess
// first: a name (matched to clippa people), No idea, or Not ours. Paste one from anywhere
// else; "Check now" fetches the sources in the background. Nothing here is ever published.

let BS = null;  // {data, days, view, source, paste, full: Set, jump, win}
// BLINDSWIN names one opening of the window (a write still in flight checks it); BLINDSREQ is the
// latest read, so an older read that answers late is dropped. A poll changes only the second.
let BLINDSWIN = 0, BLINDSREQ = 0, BPOLL = 0;
const blindsWin = win => win === BLINDSWIN && $("#tool").open && $("#tool").dataset.kind === "blinds";
const BVIEWS = [["waiting", "Waiting on you"], ["answered", "Answered"], ["no_idea", "No idea"], ["not_ours", "Not ours"],
  ["reveal", "Reveals"], ["unread", "Unread"], ["all", "All"]];
const BSTAT = { unread: "clues not read yet", waiting: "waiting on you", answered: "you guessed", no_idea: "no idea",
  not_ours: "not ours", reveal: "reveal" };

async function showBlinds(opts = {}) {
  const d = $("#tool");
  if (!BS || !d.open || d.dataset.kind !== "blinds") { BS = null; BLINDSWIN++; }  // a fresh opening
  const win = BLINDSWIN, req = ++BLINDSREQ;
  d.dataset.kind = "blinds"; d.classList.remove("cover"); d.classList.add("facesw");
  if (!BS) d.innerHTML = `<div class="m-head"><b>Blinds</b><button class="x" aria-label="Close" title="Close (Esc)" data-close>${ic("close")}</button></div><div class="tbody">Loading…</div>`;
  if (!d.open) d.showModal();
  const DEF = { days: 14, view: "waiting", source: "", paste: false, full: new Set() };
  const s = { ...DEF, ...(BS || {}), ...opts };
  try {
    const data = await api(`/api/blinds?days=${s.days}&view=${encodeURIComponent(s.view)}&source=${encodeURIComponent(s.source)}`
      + (s.jump ? `&focus=${encodeURIComponent(s.jump)}` : ""));
    if (!blindsWin(win) || req !== BLINDSREQ) return;
    BS = { ...DEF, ...(BS || {}), ...opts, data, win };  // what changed meanwhile (the paste box) stays as it is now
    renderBlinds();
  } catch (e) {
    const box = d.querySelector(".tbody");
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    else toast(e.message, true);
  }
}

function blindCard(b) {
  const full = BS.full.has(b.id), long = b.text.length > 420;
  const flags = [b.ours ? `<span class="tag bo" title="The clues could fit someone this install follows">could be ours</span>` : "",
    ...b.sensitive.map(x => `<span class="tag bs" title="A sensitive claim">${esc(x)}</span>`),
    b.private ? `<span class="tag bp" title="Points at a private person: not looked into">private</span>` : "",
    b.hidden ? `<span class="tag" title="Hidden by [blinds] hide">hidden</span>` : "",
    ...b.fields.map(f => `<span class="tag">${esc(f)}</span>`)].join("");
  const clues = b.clues.map(c => `<span class="bclue" title="${esc(c.type)}, weight ${c.weight}"><i>${esc(c.type)}</i>${esc(c.text)}</span>`).join("");
  const g = b.guesses[0];
  const guess = g ? `<div class="bguess">Your guess: <b>${esc(g.name)}</b>${g.person ? "" : ` <span class="by">(not in clippa people)</span>`}${g.why ? ` · because ${esc(g.why)}` : ""} <span class="by">${ago(g.at)}${g.who ? " · " + esc(g.who) : ""}</span>${g.note ? ` <span class="by">[${esc(g.note)}]</span>` : ""}</div>` : "";
  const answer = b.kind === "reveal" ? "" : `<div class="nbar ba">
      <input class="search bname" list="blindsPeople" maxlength="120" placeholder="Who do you think it is?" value="${esc(g?.as_written || "")}" aria-label="Your guess">
      <input class="search bwhy" maxlength="500" placeholder="Why (optional)" value="${esc(g?.why || "")}" aria-label="Why">
      <button class="btn primary" data-bguess="${esc(b.id)}">${g ? "Change guess" : "That's my guess"}</button>
      <button class="btn" data-bnone="${esc(b.id)}"${b.status === "no_idea" ? " disabled" : ""}>No idea</button>
      <button class="btn" data-bnot="${esc(b.id)}"${b.status === "not_ours" ? " disabled" : ""} title="Not one to look into">Not ours</button></div>`;
  return `<div class="nst bl${b.status === "not_ours" ? " off" : ""}" data-bid="${esc(b.id)}">
    <div class="nh"><b>${esc(b.title || b.id)}</b><span class="by">${esc(b.id)} · ${esc(b.day)} · ${esc(BSTAT[b.status] || b.status)}</span>${/^https?:\/\//i.test(b.url || "") ? `<a class="by" href="${esc(b.url)}" target="_blank" rel="noopener noreferrer">source</a>` : ""}${flags}</div>
    <div class="btext${full || !long ? " full" : ""}">${esc(b.text)}</div>
    ${long ? `<button class="lnk" data-bmore="${esc(b.id)}">${full ? "less" : "more"}</button>` : ""}
    ${b.claim ? `<div class="nctx">${esc(b.claim)}</div>` : ""}
    ${clues ? `<div class="bclues">${clues}</div>` : ""}
    ${b.twin_id ? `<div class="by">Twin: <button class="lnk" data-bjump="${esc(b.twin_id)}">${esc(b.twin_id)}</button>${b.pair_why ? " · " + esc(b.pair_why) : ""}</div>` : ""}
    ${guess}${answer}
  </div>`;
}

// What was typed into the window (not what it was filled with), so a render (a poll, another
// card's answer) doesn't lose it, and a guess changed elsewhere still shows.
function blindsTyped() {
  const out = {};
  $("#tool").querySelectorAll(".bl .ba input").forEach(el => {
    if (el.value !== el.defaultValue) out[el.closest(".bl").dataset.bid + (el.classList.contains("bwhy") ? " why" : " name")] = el.value;
  });
  out[""] = $("#blindsText")?.value;
  return out;
}
function renderBlinds() {
  const { data } = BS, job = data.job, c = data.counts, typed = blindsTyped(), focus = document.activeElement;
  const fsel = focus?.closest?.(".bl") && focus.matches("input") ? [focus.closest(".bl").dataset.bid, focus.classList.contains("bwhy") ? ".bwhy" : ".bname"]
    : focus?.id === "blindsText" ? ["", "#blindsText"] : null;
  const srcs = data.sources.map(s => `<span title="${esc(s.feed)}${s.error ? "\n" + esc(s.error) : ""}"${s.error ? ` style="color:var(--red)"` : ""}>${esc(s.name)}: ${s.fetched_at ? ago(s.fetched_at) : "never fetched"}${s.error ? " (failing)" : ""}</span>`).join(" · ");
  const head = data.sources_error ? `<span style="color:var(--red)">${esc(data.sources_error)}</span>`
    : !data.sources.length ? "no sources: add [[blinds.sources]] to clipper.toml (Blind-items on the wiki); pasting still works" : srcs;
  const n = v => c[v] || 0;
  const tabs = BVIEWS.map(([v, label]) => `<button data-bview="${v}" class="${v === BS.view ? "on" : ""}">${label} <span class="n">${n(v)}</span></button>`).join("");
  const srcOpts = [["", "every source"], ...data.sources.map(s => [s.name, s.name]), ["hand", "pasted by hand"]]
    .map(([v, l]) => `<option value="${esc(v)}"${v === BS.source ? " selected" : ""}>${esc(l)}</option>`).join("");
  const days = [1, 3, 7, 14, 30, 90, 365].map(x => `<option value="${x}"${x == BS.days ? " selected" : ""}>${x} day${x > 1 ? "s" : ""}</option>`).join("");
  const log = job.running || job.error || job.result ? `<div class="nctx blog">${job.running ? "Checking… " : job.error ? `<span style="color:var(--red)">${esc(job.error)}</span> ` : "Last check: "}${job.result ? esc(`${job.result.new} new, ${job.result.read} read, ${job.result.paired} paired`) : ""} <span class="by">${ago(job.at)}</span>${job.log.length ? `<pre>${esc(job.log.slice(-8).join("\n"))}</pre>` : ""}</div>` : "";
  $("#tool").innerHTML = `<div class="m-head"><b>Blinds</b><span style="color:var(--faint);font-size:12px">${head} · clues by ${esc(data.clues_model)}${data.hidden ? ` · ${data.hidden} hidden by [blinds] hide` : ""}</span><button class="x" aria-label="Close" title="Close (Esc)" data-close>${ic("close")}</button></div>
    <div class="news blinds">
      <div class="nbar"><div class="seg">${tabs}</div>
        <select id="blindsDays" aria-label="How far back">${days}</select><select id="blindsSource" aria-label="Source">${srcOpts}</select>
        <button class="btn" id="blindsPasteBtn">Paste a blind…</button>
        <button class="btn" id="blindsCheck"${job.running ? " disabled" : ""} title="Fetch the sources and read the new blinds' clues (Haiku, about $0.0002 a blind)">Check now</button></div>
      ${BS.paste ? `<div class="nst"><textarea id="blindsText" rows="5" maxlength="6000" placeholder="A blind from anywhere else (Instagram, X, a screenshot's text)"></textarea>
        <div class="nbar"><button class="btn primary" id="blindsAdd">Add blind</button><button class="btn" id="blindsPasteX">Cancel</button><span class="by">its clues are read now, a fraction of a cent</span></div></div>` : ""}
      ${log}
      ${data.more ? `<div class="by">Showing the newest ${data.blinds.length}; ${data.more} more: fewer days or one source shows them.</div>` : ""}
      ${data.blinds.map(blindCard).join("") || `<div class="empty">Nothing ${BS.view === "all" ? "" : `${(BVIEWS.find(v => v[0] === BS.view) || [, ""])[1].toLowerCase()} `}in the last ${BS.days} day(s).</div>`}
    </div>
    <datalist id="blindsPeople">${data.people.map(p => `<option value="${esc(p)}">`).join("")}</datalist>`;
  $("#tool").querySelectorAll(".bl .ba input").forEach(el => {
    const k = el.closest(".bl").dataset.bid + (el.classList.contains("bwhy") ? " why" : " name");
    if (k in typed) el.value = typed[k];
  });
  if (typed[""] && $("#blindsText")) $("#blindsText").value = typed[""];
  if (fsel) (fsel[0] ? $(fsel[1], $("#tool").querySelector(`[data-bid="${CSS.escape(fsel[0])}"]`) || document.createElement("div")) : $(fsel[1]))?.focus();
  if (BS.jump) {
    const el = $("#tool").querySelector(`[data-bid="${CSS.escape(BS.jump)}"]`);
    if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("bhit"); }
    BS.jump = "";
  }
  clearTimeout(BPOLL);  // one poll at a time while a check runs
  if (job.running) { const win = BS.win; BPOLL = setTimeout(() => { if (BS && blindsWin(win)) showBlinds(); }, 3000); }
}

async function blindsAnswer(t, body, msg) {
  const card = t.closest(".bl"), win = BS.win;
  t.disabled = true;
  try {
    await api("/api/blinds/answer", { id: card.dataset.bid, ...body });
    toast(msg);
    if (BS && blindsWin(win)) showBlinds();
  } catch (e) { toast(e.message, true); t.disabled = false; }
}

document.addEventListener("click", async e => {
  const t = e.target.closest?.("#blindsBtn,[data-bview],[data-bguess],[data-bnone],[data-bnot],[data-bmore],[data-bjump],#blindsPasteBtn,#blindsPasteX,#blindsAdd,#blindsCheck");
  if (!t) return;
  if (t.id === "blindsBtn") return showBlinds();
  if (!BS) return;
  const ds = t.dataset;
  if (ds.bview) return showBlinds({ view: ds.bview });
  if (ds.bmore) { BS.full.has(ds.bmore) ? BS.full.delete(ds.bmore) : BS.full.add(ds.bmore); return renderBlinds(); }
  if (ds.bjump) return showBlinds({ view: "all", source: "", jump: ds.bjump });  // the server adds it if out of range
  if (ds.bguess) {
    const card = t.closest(".bl"), name = $(".bname", card).value.trim();
    if (!name) { $(".bname", card).focus(); return toast("Type who you think it is, or say No idea.", true); }
    return blindsAnswer(t, { name, why: $(".bwhy", card).value.trim() }, `Your guess: ${name}`);
  }
  if (ds.bnone) return blindsAnswer(t, { none: true }, "No idea");
  if (ds.bnot) return blindsAnswer(t, { not_ours: true }, "Not ours");
  if (t.id === "blindsPasteBtn") { BS.paste = true; renderBlinds(); return $("#blindsText")?.focus(); }
  if (t.id === "blindsPasteX") { BS.paste = false; return renderBlinds(); }
  if (t.id === "blindsAdd") {
    const text = $("#blindsText").value.trim();
    if (!text) return toast("Paste the blind's text first.", true);
    t.disabled = true;
    const win = BS.win;  // reading its clues takes a few seconds; the window may close meanwhile
    try {
      const r = await api("/api/blinds/paste", { text });
      toast(`Added ${r.id}`);
      if (BS && blindsWin(win)) showBlinds({ paste: false, view: "all", source: "", jump: r.id });
    } catch (err) { toast(err.message, true); if (BS && blindsWin(win)) t.disabled = false; }
    return;
  }
  if (t.id === "blindsCheck") {
    t.disabled = true;
    const win = BS.win;
    try {
      const data = await api("/api/blinds/check", { days: BS.days, view: BS.view, source: BS.source });
      if (BS && blindsWin(win)) { BS.data = data; renderBlinds(); }
    } catch (err) { toast(err.message, true); if (BS && blindsWin(win)) t.disabled = false; }
  }
});
document.addEventListener("change", e => {
  if (!BS) return;
  if (e.target.id === "blindsDays") showBlinds({ days: +e.target.value });
  if (e.target.id === "blindsSource") showBlinds({ source: e.target.value });
});
document.addEventListener("keydown", e => {
  if (e.key !== "Enter" || !BS || !e.target.matches?.(".bl .ba input")) return;
  e.preventDefault();
  $("[data-bguess]", e.target.closest(".bl")).click();
});
$("#tool").addEventListener("close", () => { if ($("#tool").dataset.kind === "blinds") { BS = null; BLINDSWIN++; clearTimeout(BPOLL); } });
