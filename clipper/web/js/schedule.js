// War room: the Schedule page. Next up (the next five to go out), then the Week (seven days from
// today, hours down, a page per week out to the scheduler's horizon), the Timeline (2 days back to
// 7 ahead, a lane per show) or the day List, beside the tray of approved items with no slot.
// Dragging onto a slot, a day or "unpin" is panels.js's (#20): it pins through the same action as
// Kitsu's Publish at. A phone gets the List.

const SVIEWS = ["week", "timeline", "list"];
let SVIEW = (() => { try { const v = localStorage.getItem("warroom.sview"); return SVIEWS.includes(v) ? v : "week"; } catch { return "week"; } })();
let WEEK = 0;  // weeks after this one
const SKIND = { ...KIND, clip: "Clip" };  // schedule.json says clip where an item says pick
const addDays = (day, n) => { const [y, m, d] = day.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10); };
const tzAt = iso => { const p = localPin(iso); return { day: p.slice(0, 10), h: +p.slice(11, 13), m: +p.slice(14, 16) }; };
const tm = iso => new Date(iso).toLocaleTimeString([], { timeZone: STATE.timezone, hour: "numeric", minute: "2-digit" });
const dayName = (day, o) => new Date(day + "T12:00:00").toLocaleDateString([], o);
const hueOf = name => (STATE.shows || []).find(s => s.name === name)?.hue || 0;
function inTime(iso) {
  const s = (new Date(iso) - Date.now()) / 1000;
  if (s <= 0) return ago(iso);
  return s < 3600 ? `in ${Math.max(1, Math.round(s / 60))} min` : s < 129600 ? `in ${Math.round(s / 3600)} h` : `in ${Math.round(s / 86400)} d`;
}
// What an entry is, in words: for a block's tooltip and the Next up cards.
function entryState(e) {
  const err = (STATE.uploads?.next || []).find(x => x.key === e.key)?.last_error;
  if (err) return `the last upload failed: ${err}`;
  if (e.status !== "uploaded") return `not uploaded yet${e.source === "pinned" ? ", pinned" : e.source === "asap" ? ", as soon as it may" : ""}`;
  const pub = new Date(e.publish_at) <= Date.now();
  return `${e.uploaded_at ? `uploaded ${ago(e.uploaded_at)}` : "uploaded"}, ${pub ? "public" : "private until it goes public"}${e.views != null ? ` · ${Number(e.views).toLocaleString()} views` : ""}`;
}

function renderSchedule() {
  // an item picked up to pin that got a slot meanwhile (a replan, the bot) is put down (panels.js)
  if (ARMED && !STATE.items.some(i => i.ref === ARMED.ref && STATUS.unslotted(i))) arm(null, false);
  const view = PHONE.matches ? "list" : SVIEW, week = view === "week";
  $("#schedSub").textContent = `${STATE.counts.scheduled_7d} in 7 days`;
  for (const b of document.querySelectorAll("[data-sview]")) b.setAttribute("aria-pressed", String(b.dataset.sview === view));
  $("#week").hidden = $("#wkNav").hidden = !week;
  $("#tl").hidden = view !== "timeline";
  $("#schedule").hidden = view !== "list";
  renderNextUp(); renderTray();
  const segs = STATE.items.some(i => STATUS.unslotted(i) && i.kind === "segment" && !i.hold);
  $("#schedTools").innerHTML = `<label title="Show the open slots to drop items on"><input type="checkbox" id="showFree" ${SHOWFREE ? "checked" : ""}> Open slots</label>
    <span class="unpin" data-unpin>${ic("down")}drop here to unpin</span>
    ${segs ? `<button class="btn" data-nextkind="segment" title="Pin each approved segment without a slot to the next open segment slot, before it goes stale (a preview first)">${ic("next")}Segments to open slots…</button>` : ""}
    <span>max ${STATE.rules?.max_per_day ?? "?"}/day · ${STATE.rules?.min_gap_minutes ?? "?"} min apart · ${PHONE.matches ? "tap one in Ready to slot, then a slot" : "drag a block to move it"}</span>
    ${week ? `<span class="legend"><span><i class="lg up"></i>uploaded</span><span><i class="lg planned"></i>not uploaded yet</span><span><i class="lg open"></i>open slot</span><span>${ic("pin")}pinned</span></span>`
    : view === "timeline" ? `<span class="legend"><span><i class="lg up"></i>uploaded</span><span><i class="lg planned"></i>not uploaded yet</span><span><i class="lg quiet"></i>nothing goes out (Kick first)</span><span><i class="lg band"></i>the last 24 h</span></span>` : ""}`;
  if (week) renderWeek(); else if (view === "timeline") renderTimeline(); else renderDays();
}

function renderNextUp() {
  const next = STATE.schedule.filter(e => new Date(e.publish_at) > Date.now()).slice(0, 5), u = STATE.uploads;
  $("#nextUp").innerHTML = `<div class="lead"><b>Next up</b>${u ? `<span>${u.room} of ${u.max_per_24h} upload room now</span>` : ""}</div>`
    + (next.length ? next.map(e => { const err = entryState(e).startsWith("the last upload failed");
      return `<button class="nu${err ? " failed" : ""}" data-open="${esc(e.key)}" title="${esc(`${e.title}\n${entryState(e)}`)}">${e.thumbnail_url ? `<img loading="lazy" src="${esc(e.thumbnail_url)}" alt="">` : "<i></i>"}
        <span class="b"><span class="w">${esc(dayName(tzAt(e.publish_at).day, { weekday: "short" }))} ${esc(tm(e.publish_at))} · ${esc(inTime(e.publish_at))}${err ? ` · ${ic("warn")}failed` : ""}</span>
        <span class="t">${esc(bare(e.title, e.show_name))}</span><span class="sub">${showTag("", e.show_name)}<span>${esc(SKIND[e.kind] || e.kind)}</span></span></span></button>`; }).join("")
    : `<div class="empty">Nothing is planned ahead. Replan fills the open slots.</div>`);
}

// The tray: what can still go out and has no slot, the number the Schedule tab shows. Stale and
// over-budget items won't go out unless pinned: one link to them in the queue, not in the tray.
function renderTray() {
  const uns = STATE.items.filter(STATUS.unslotted).sort(newest);
  const gone = STATE.items.filter(i => i.decision === "approved" && !i.scheduled && !i.uploaded && i.dropped).length;
  $("#traySub").textContent = uns.length || "";
  $("#unslotted").innerHTML = (uns.length ? `<div class="ss">Drag one onto an open slot, or tap it and then the slot; ${ic("next")} for the next one</div>`
    + uns.slice(0, 60).map(i => { const k = i.kind === "pick" ? "clip" : i.kind;
      return `<div class="chip${ARMED?.ref === i.ref ? " armed" : ""}" draggable="true" data-dref="${esc(i.ref)}" data-dkind="${esc(k)}" title="${esc(i.title)}"><span class="bar ${esc(k)}"></span><span class="t"><span class="tt">${esc(bare(i.title, i.show_name))}</span><span class="sub">${showTag(i.show, i.show_name)}<span>${esc(SKIND[k] || k)}</span>${i.hold ? `<span class="why">held</span>` : ""}</span></span>${!i.hold && i.kind !== "compilation" ? `<button class="nx" data-nextslot="${esc(i.ref)}" title="Pin to the next open slot (a preview first)" aria-label="Next open slot">${ic("next")}</button>` : "<span></span>"}</div>`; }).join("")
    + (uns.length > 60 ? `<div class="ss">and ${uns.length - 60} more</div>` : "")
    : `<div class="ss">Nothing approved is waiting for a slot.</div>`)
    + (gone ? `<button class="lnk" data-notout title="Stale or over their stream's budget: the scheduler leaves them out, a pin still sends one">${gone} not going out (stale or over budget)</button>` : "");
}

function renderWeek() {
  const today = tzAt(new Date().toISOString()).day, now = tzAt(new Date().toISOString());
  const last = Math.max(0, Math.ceil((STATE.rules?.horizon_days || 14) / 7) - 1);
  WEEK = Math.max(0, Math.min(WEEK, last));
  const days = [...Array(7)].map((_, k) => addDays(today, 7 * WEEK + k)), inWeek = x => days.includes(x.day);
  const es = STATE.schedule.map(e => ({ e, ...tzAt(e.publish_at) })).filter(inWeek);
  const fs = freeShown() ? (STATE.free_slots || []).map(f => ({ f, ...tzAt(f.at) })).filter(inWeek) : [];
  const hrs = [...es, ...fs].map(x => x.h).concat(days.includes(now.day) ? [now.h] : []);
  const h0 = hrs.length ? Math.max(0, Math.min(...hrs) - 1) : 8, h1 = hrs.length ? Math.min(23, Math.max(...hrs) + 1) : 22;
  const max = STATE.rules?.max_per_day || 0;
  $("#wkLabel").textContent = `${dayName(days[0], { month: "short", day: "numeric" })} – ${dayName(days[6], { month: "short", day: "numeric" })}`;
  $("#wkPrev").disabled = WEEK === 0; $("#wkNext").disabled = WEEK === last;
  const blk = ({ e }) => { const up = e.status === "uploaded", n = hueOf(e.show_name), k = SKIND[e.kind] ? e.kind : "clip";
    return `<div class="blk ${up ? "up" : "planned"}${new Date(e.publish_at) < Date.now() ? " past" : ""}${n ? ` s${n}` : ""} k-${k}" data-open="${esc(e.key)}" role="button" tabindex="0"
      title="${esc(`${tm(e.publish_at)} · ${e.show_name} · ${SKIND[e.kind] || e.kind}\n${e.title}\n${entryState(e)}`)}"${up ? "" : ` draggable="true" data-dref="${esc(e.key)}" data-dkind="${esc(e.kind)}" data-dat="${esc(e.publish_at)}"`}>
      <span class="bm">${esc(tm(e.publish_at))}<span>${esc(SKIND[e.kind] || e.kind)}</span>${e.source === "pinned" ? ic("pin") : ""}${entryState(e).startsWith("the last upload failed") ? ic("warn") : ""}</span>
      <span class="bt">${esc(bare(e.title, e.show_name))}</span></div>`; };
  const slot = ({ f }) => `<div class="fslot k-${esc(f.kind)}" data-dslot="${esc(f.at)}" data-dkind="${esc(f.kind)}" title="An open ${esc(SKIND[f.kind] || f.kind)} slot: drop an item here to pin it">${esc(tm(f.at))} ${esc(SKIND[f.kind] || f.kind)}</div>`;
  let h = `<div class="wk"><div class="wh corner"></div>`;
  for (const d of days) {
    const n = es.filter(x => x.day === d).length;
    h += `<div class="wh${d === today ? " today" : ""}" data-dday="${d}" title="Drop here to pick a time on this day"><b>${esc(dayName(d, { weekday: "short", day: "numeric" }))}${d === today ? " · today" : ""}</b>
      <span class="c${max && n >= max ? " full" : ""}">${n}${max ? ` of ${max}` : ""}</span>${max ? `<span class="capbar"><i style="width:${Math.min(100, 100 * n / max)}%"></i></span>` : ""}</div>`;
  }
  for (let hr = h0; hr <= h1; hr++) {
    h += `<div class="hr">${esc(new Date(2000, 0, 1, hr).toLocaleTimeString([], { hour: "numeric" }))}</div>`;
    for (const d of days) {
      const here = x => x.day === d && x.h === hr;
      const rows = [...es.filter(here).map(x => ({ m: x.m, html: blk(x) })), ...fs.filter(here).map(x => ({ m: x.m, html: slot(x) }))].sort((a, b) => a.m - b.m);
      h += `<div class="cell${d === today ? " today" : ""}">${d === now.day && hr === now.h ? `<i class="nowline" style="top:${(now.m / 60 * 100).toFixed(1)}%" title="Now"></i>` : ""}${rows.map(r => r.html).join("")}</div>`;
    }
  }
  $("#week").innerHTML = h + "</div>";
}

// 00:00 on a day in the schedule's zone, as epoch ms (a DST change at 2 am doesn't move midnight).
function tzMidnight(day) {
  const g = Date.parse(day + "T00:00:00Z");
  return 2 * g - Date.parse(localPin(new Date(g).toISOString()).replace(" ", "T") + ":00Z");
}

// The Timeline: why a show got nothing and how close the cap is. A lane of recordings on top (the
// hatched stretch after each is kick_first_hours, when none of its items may go out), a lane per
// show with a tick per entry, the uploads of the last two days, and the open slots; the shaded
// band is the rolling 24 h that max_per_24h counts.
function renderTimeline() {
  const now = Date.now(), today = tzAt(new Date(now).toISOString()).day;
  const days = [...Array(10)].map((_, k) => addDays(today, k - 2)), mid = days.map(tzMidnight);
  const t0 = now - 2 * 864e5, t1 = now + 7 * 864e5;
  const X = t => `${(Math.max(0, Math.min(1, (t - t0) / (t1 - t0))) * 100).toFixed(3)}%`;
  const W = (a, b) => `left:${X(a)};width:calc(${X(b)} - ${X(a)})`;
  const u = STATE.uploads || {}, shows = STATE.shows || [], hueK = key => shows.find(x => x.key === key)?.hue || 0;
  const when = t => `${dayName(tzAt(new Date(t).toISOString()).day, { weekday: "short" })} ${tm(new Date(t).toISOString())}`;
  // what every lane shares: the day lines, the 24 h band and now
  const under = mid.map((t, k) => t > t0 && t < t1 ? `<i class="gl${days[k] === today ? " today" : ""}" style="left:${X(t)}"></i>` : "").join("")
    + `<i class="cap" style="${W(now - 864e5, now)}"></i>`;
  const over = `<i class="tnow" style="left:${X(now)}"></i>`;
  const lane = (label, sub, body, cls = "") => `<div class="ln${cls}">${label}<span class="lsub">${sub}</span></div><div class="lt${cls}">${under}${body}${over}</div>`;
  const streams = (STATE.streams || []).map(s => {
    const a = Date.parse(s.started_at), live = !s.ended_at && ["recording", "paused", "finalizing"].includes(s.recording);
    const b = s.ended_at ? Date.parse(s.ended_at) : live ? now : NaN, r = s.release_at ? Date.parse(s.release_at) : NaN;
    return { s, a, b, r, live, n: hueK(s.show) };
  }).filter(x => x.a < t1 && Math.max(x.b || 0, x.r || 0) > t0);
  for (const l of LIVE)  // live now with no recording on the page (a show that isn't recorded): from /api/live
    if (l.live && l.since && !streams.some(x => x.live && x.s.show === l.show))
      streams.push({ s: { show: l.show, show_name: l.show_name, title: l.title || "" }, a: kickMs(l.since), b: now, r: NaN, live: true, norec: !recording(l), n: hueK(l.show) });
  const quiet = x => x.r > x.b ? `<i class="quiet" style="${W(x.b, x.r)}" title="${esc(`${x.s.show_name}: nothing from this stream goes out until ${when(x.r)} (Kick first)`)}"></i>` : "";
  let h = `<div class="tl"><div class="ln ax"></div><div class="lt ax">${mid.map((t, k) => t >= t0 && t < t1 - 6 * 36e5 ? `<span class="${days[k] === today ? "today" : ""}" style="left:${X(t)}">${esc(dayName(days[k], { weekday: "short", day: "numeric" }))}</span>` : "").join("")}<span class="nowl" style="left:${X(now)}">now</span></div>`;
  h += lane("<b>Recordings</b>", `${streams.length} in view`, streams.map(x => quiet(x)
    + (x.b > t0 ? `<span class="sbar${x.n ? ` s${x.n}` : ""}${x.live && x.s.recording !== "finalizing" ? " live" : ""}${x.norec ? " norec" : ""}" style="${W(Math.max(t0, x.a), x.b)}" title="${esc(`${x.s.show_name} · ${x.s.title}\n${when(x.a)} – ${x.norec ? "live now, not recorded" : x.live ? ({ paused: "paused (the show went offline)", finalizing: "ended, the file is closing" }[x.s.recording] || "still recording") : when(x.b)}${x.r ? `\nits items may go out from ${when(x.r)}` : ""}`)}">${esc(x.s.show_name)}</span>` : "")).join(""));
  const es = STATE.schedule.map(e => ({ e, t: Date.parse(e.publish_at) })).filter(x => x.t >= t0 && x.t <= t1);
  const tick = ({ e, t }) => { const up = e.status === "uploaded", n = hueOf(e.show_name), k = SKIND[e.kind] ? e.kind : "clip";
    const failed = entryState(e).startsWith("the last upload failed");
    return `<i class="tk ${up ? "up" : "planned"}${t < now ? " past" : ""}${failed ? " failed" : ""}${n ? ` s${n}` : ""} k-${k}" style="left:${X(t)}" data-open="${esc(e.key)}" role="button" tabindex="0"
      title="${esc(`${when(t)} · ${e.show_name} · ${SKIND[e.kind] || e.kind}\n${e.title}\n${entryState(e)}`)}"${up ? "" : ` draggable="true" data-dref="${esc(e.key)}" data-dkind="${esc(e.kind)}" data-dat="${esc(e.publish_at)}"`}></i>`; };
  const named = new Set(shows.map(x => x.name));
  for (const sh of [...shows, ...(es.some(x => !named.has(x.e.show_name)) ? [{ name: "", hue: 0 }] : [])]) {
    const mine = es.filter(x => sh.name ? x.e.show_name === sh.name : !named.has(x.e.show_name)), ahead = mine.filter(x => x.t > now).length;
    h += lane(`<b class="show${sh.hue ? ` s${sh.hue}` : ""}">${esc(sh.name || "Other")}</b>`,
      ahead ? `${ahead} ahead` : `<span class="none" title="Nothing of this show is planned in the next 7 days: see its recordings' Kick-first stretch above, and the queue's approved and dropped">nothing ahead</span>`,
      streams.filter(x => sh.key && x.s.show === sh.key).map(quiet).join("") + mine.map(tick).join(""));
  }
  const ups = STATE.schedule.filter(e => e.uploaded_at && Date.parse(e.uploaded_at) >= t0).map(e => Date.parse(e.uploaded_at));
  const frees = u.frees_at ? Date.parse(u.frees_at) : NaN;
  h += lane("<b>Uploads</b>", u.max_per_24h ? `<span${u.room ? "" : ` class="none"`}>${u.last_24h} of ${u.max_per_24h} in 24 h</span>` : "",
    ups.map(t => `<i class="ut" style="left:${X(t)}" title="uploaded ${esc(when(t))}"></i>`).join("")
    + (frees > now && frees < t1 ? `<i class="free-at" style="left:${X(frees)}" title="${esc(`room for another upload from ${when(frees)}`)}"></i>` : ""), " ups");
  if (freeShown()) {
    const fs = (STATE.free_slots || []).filter(f => Date.parse(f.at) >= t0 && Date.parse(f.at) <= t1);
    h += lane("<b>Open slots</b>", `${fs.length} in view`, fs.map(f => `<i class="ft k-${esc(f.kind)}" style="left:${X(Date.parse(f.at))}" data-dslot="${esc(f.at)}" data-dkind="${esc(f.kind)}" title="${esc(`An open ${SKIND[f.kind] || f.kind} slot, ${when(Date.parse(f.at))}: drop an item here to pin it`)}"></i>`).join(""), " slots");
  }
  $("#tl").innerHTML = h + "</div>";
}

// The List: the days one under the other (the phone's view, and the desktop's other one). On a
// phone it is an agenda: what is still to come, the earlier ones folded into one row, no empty
// days, and the open slots only while an item is picked up to pin (panels.js freeShown).
let EARLIER = false;  // the agenda's folded rows, shown
function renderDays() {
  const tz = STATE.timezone, days = {}, agenda = PHONE.matches, now = Date.now();
  const dayKey = d => new Date(d).toLocaleDateString("en-CA", { timeZone: tz });
  const today = dayKey(new Date());
  for (const e of STATE.schedule) (days[dayKey(e.publish_at)] ??= []).push(e);
  for (let k = 0; k < 14; k++) { const d = new Date(now + k * 864e5); days[dayKey(d)] ??= []; }
  const free = {};
  if (freeShown()) for (const f of STATE.free_slots || []) if (new Date(f.at) < now + 14 * 864e5) (free[dayKey(f.at)] ??= []).push(f);
  for (const d of Object.keys(free)) days[d] ??= [];
  const gone = (d, e) => d < today || (d === today && Date.parse(e.publish_at) <= now);
  const fold = agenda && !EARLIER, folded = agenda ? Object.entries(days).reduce((n, [d, es]) => n + es.filter(e => gone(d, e)).length, 0) : 0;
  $("#schedule").innerHTML = (folded ? `<button class="earlier" data-earlier aria-expanded="${EARLIER}">${ic(EARLIER ? "up" : "down")}${EARLIER ? "Hide the earlier ones" : `${folded} earlier (uploaded or due)`}</button>` : "")
    + Object.keys(days).sort().filter(d => !fold || d >= today).map(d => {
    const label = new Date(d + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
    const rows = [...days[d].filter(e => !fold || !gone(d, e)).map(e => ({ e, at: e.publish_at })), ...(free[d] || []).map(f => ({ f, at: f.at }))]
      .sort((a, b) => a.at.localeCompare(b.at));
    if (agenda && !rows.length && d !== today) return "";
    return `<div class="day ${d === today ? "today" : d < today ? "past" : ""}"><h3 data-dday="${d}">${d === today ? "Today · " : ""}${label}<span class="n">${days[d].length || ""}</span></h3>
      ${rows.map(({ e, f }) => f ? `<div class="slot free" data-dslot="${esc(f.at)}" data-dkind="${esc(f.kind)}">
        <span class="tm">${tm(f.at)}</span>
        <span class="bar ${esc(f.kind)}"></span><span><div class="tt">open ${esc(f.kind)} slot</div></span></div>`
      : `<div class="slot" data-open="${esc(e.key)}" role="button" tabindex="0" ${e.status !== "uploaded" ? `draggable="true" data-dref="${esc(e.key)}" data-dkind="${esc(e.kind)}" data-dat="${esc(e.publish_at)}"` : ""}>
        <span class="tm">${tm(e.publish_at)}</span>
        <span class="bar ${esc(e.kind)}"></span>
        <span><div class="tt" title="${esc(e.title)}">${esc(bare(e.title, e.show_name))}</div><div class="ss">${showTag("", e.show_name)} · ${e.kind}${e.status === "uploaded" ? ` · <span class="up">uploaded</span>${e.views != null ? ` · ${Number(e.views).toLocaleString()} views` : ""}${e.url ? ` · <a href="${esc(e.url)}" target="_blank" rel="noopener">YouTube</a>` : ""}` : e.source === "pinned" ? " · pinned" : ""}</div></span></div>`).join("")
      || `<div class="slot" style="cursor:default"><span></span><span></span><span class="ss">—</span></div>`}</div>`;
  }).join("");
}

document.addEventListener("click", e => {
  if (e.target.closest?.("[data-earlier]")) { EARLIER = !EARLIER; if (STATE) renderSchedule(); return; }
  const v = e.target.closest?.("[data-sview]");
  if (v) { SVIEW = v.dataset.sview; try { localStorage.setItem("warroom.sview", SVIEW); } catch { /* private window */ } if (STATE) renderSchedule(); return; }
  const w = e.target.closest?.("#wkPrev,#wkNext");
  if (w) { WEEK += w.id === "wkNext" ? 1 : -1; if (STATE) renderSchedule(); return; }
  if (e.target.closest?.("[data-notout]")) {
    goPage("queue");
    if (document.body.dataset.view !== "queue") return;  // an item window that wouldn't close
    TAB = "approved"; ST = "dropped"; SEL = null; renderQueue();
  }
});
PHONE.addEventListener("change", () => { if (STATE) renderSchedule(); });
