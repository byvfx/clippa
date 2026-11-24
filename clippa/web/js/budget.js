// OpenRouter budget on the Money page. The server (clippa/openrouter_budget.py) holds the keys.

let ORB = null;
let ORB_FORM = null;  // the limit or warn step, so Back restores what was typed

function usd2(v) { return v == null || v === "" ? "—" : "$" + (+v).toFixed(2); }
function budgetHealth() {
  if (!ORB || ORB.header?.health !== "stopped") return null;
  return { level: "bad", label: "Stopped", why: ORB.banner?.title || "The clippa key's limit is spent." };
}
function paintSpend() {
  const b = $("#wSpend")?.querySelector("b");
  if (!b) return;
  const spend = ORB?.header?.spend;
  b.style.color = spend === "bad" ? "var(--red)" : spend === "warn" ? "var(--amber)" : "";
}
// The health light is owned by tick() (core.js). Applying a budget answer calls it now:
// the overview poll and this one finish in either order, and the 1 s clock would otherwise
// leave #wHealth on OK for up to a second after the cap is already spent.
function applyOpenRouter(data) {
  ORB = data;
  paintSpend();
  paintBadge();
  renderOrb();
  if (typeof tick === "function") tick();
}
function paintBadge() {
  const on = !!ORB?.header?.badge;
  for (const id of ["vnMoney", "vnMoneyMore"]) {
    const el = $("#" + id);
    if (!el) continue;
    el.hidden = !on;
    el.textContent = on ? "!" : "";
  }
}
function orbAge(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (Number.isNaN(s)) return "";
  if (s < 90) return Math.max(0, Math.round(s)) + " s ago";
  return ago(iso);
}
function retryLeft() {
  const until = ORB?.retry?.until;
  if (!until) return 0;
  return Math.max(0, Math.ceil((new Date(until) - Date.now()) / 1000));
}

async function refreshBudget(fresh) {
  if (!$("#orb")) return;
  try { applyOpenRouter(await api("/api/openrouter" + (fresh ? "?fresh=1" : ""))); }
  catch (e) {
    applyOpenRouter({ error: e.message, configured: false, level: "error", links: { credits: "https://openrouter.ai/settings/credits" },
            header: { spend: "ok", health: null, badge: false }, events: [] });
  }
}

function renderOrb() {
  const d = ORB || {};
  const sub = d.stale ? "last good answer · OpenRouter didn't reply" : d.fetched_at ? `clippa key · from OpenRouter ${orbAge(d.fetched_at)}` : "";
  $("#orb").innerHTML = `<h2>OpenRouter budget <span class="sp">${esc(sub)}</span>
      <button class="btn sm" type="button" data-orb="refresh" title="Ask OpenRouter again">${ic("sync")}</button></h2>
    ${bannerHtml(d)}${d.configured && d.key ? columns(d) : emptyState(d)}${eventsHtml(d)}`;
}

function bannerHtml(d) {
  const b = d.banner;
  const banner = b ? `<div class="orb-banner ${esc(b.kind)}">${ic(b.kind === "bad" ? "stop" : "warn")}
      <div class="tx"><b>${esc(b.title)}</b><span class="d">${esc(b.body)}</span></div>
      <div class="acts">${bannerActs(d)}</div></div>` : "";
  const retry = d.retry ? `<div class="retry">${ic("wait")}<span><b>Retrying in <span class="mono" id="orbRetryN">${retryLeft()} s</span></b> · ${esc(d.retry.body)}</span></div>` : "";
  return banner + retry;
}
function bannerActs(d) {
  if (d.level === "stopped") {
    const edit = editBtn(d, "Raise limit…");
    const top = `<a class="btn xs" href="${esc(d.links?.credits || "https://openrouter.ai/settings/credits")}" target="_blank" rel="noopener">Top up on OpenRouter ${ic("external")}</a>`;
    return `${edit}${top}<button class="btn xs" type="button" data-go="pipeline">${ic("pipeline")}Pipeline</button>`;
  }
  if (d.level === "warn") return editBtn(d, "Edit limit…");
  return "";
}
function editBtn(d, label) {
  if (d.can_edit) return `<button class="btn warn xs" type="button" data-orb="limit">${ic("edit")}${esc(label)}</button>`;
  return `<button class="btn warn xs" type="button" disabled title="${esc(d.edit_why || "Read only")}">${ic("edit")}${esc(label)}</button>`;
}

function emptyState(d) {
  const msg = d.missing || d.error || "OpenRouter didn't answer.";
  const href = d.links?.credits || "https://openrouter.ai/settings/credits";
  return `<div class="orb-body"><div class="orb-col"><div class="sub">Clippa key</div>
    <div>${esc(msg)}</div>
    <div><a class="ext" href="${esc(href)}" target="_blank" rel="noopener">Top up on OpenRouter ${ic("external")}</a>
      <div class="f" style="margin-top:3px">${esc(d.topup || "Buying credits and auto top-up are set on OpenRouter. Its API can't change them.")}</div></div>
    </div></div>`;
}

function pills(d) {
  const k = d.key;
  if (k.disabled) return `<span class="pill bad"><i></i>Disabled</span>`;
  if (d.level === "stopped") return `<span class="pill bad"><i></i>Limit reached</span>`;
  const used = d.level === "warn" && k.pct != null ? `<span class="pill warn"><i></i>${Math.round(k.pct)}% used</span>` : "";
  return `<span class="pill ok"><i></i>Enabled</span>${used}`;
}
function columns(d) {
  const k = d.key, href = d.links?.credits || "https://openrouter.ai/settings/credits";
  const of = k.limit_usd != null ? `used ${esc(k.used_label)} of a <b>${usd2(k.limit_usd)}</b> cap` : `used ${esc(k.used_label)}`;
  const leftCls = k.meter === "over" ? "bad" : k.meter || "";
  const meter = k.limit_usd != null ? `<div><div class="capmeter"><i class="${esc(k.meter)}" style="width:${Math.min(100, k.pct || 0)}%"></i><span class="tick" style="left:${k.warn_pct}%" data-l="warn ${esc(k.warn_pct)}%"></span></div>
        <div class="capfoot"><span class="left ${leftCls}">${usd2(k.left_usd)} left</span><span class="f">${esc(k.reset_text)}</span></div></div>` : `<div class="f">No spending limit on this key.</div>`;
  const tiles = (d.periods || []).map(p => `<div class="tile ${p.current ? "cur" : ""}"${p.current ? ` title="The period the cap counts"` : ""}><span>${esc(p.label)}</span><b>${usd2(p.usd)}</b></div>`).join("");
  const mgmt = d.management || {};
  const power = k.disabled ? "enable" : "disable";
  const powerLabel = k.disabled ? "Enable key…" : "Disable key…";
  const powerBtn = d.can_edit
    ? `<button class="btn ${k.disabled ? "" : "bad"} xs" type="button" data-orb="${power}">${ic("power")}${powerLabel}</button>`
    : `<button class="btn bad xs" type="button" disabled title="${esc(d.edit_why || "")}">${ic("power")}${powerLabel}</button>`;
  return `<div class="orb-body">
    <div class="orb-col">
      <div class="sub">Clippa key <span class="r">${pills(d)}</span></div>
      <div class="keyline"><b>${esc(k.name)}</b>${k.label ? `<span class="mono">${esc(k.label)}</span>` : ""}</div>
      <div class="bigrow"><span class="big">${usd2(k.used_usd)}</span><span class="of">${of}</span></div>
      ${meter}
      <div class="periods">${tiles}</div>
      <div class="f">${esc(d.figures || "")}</div>
    </div>
    <div class="orb-col">
      <div class="sub">Limits</div>
      <div class="lim">
        <div class="top">${ic("money")}<b>OpenRouter key cap</b>${editBtn(d, "Edit limit…")}</div>
        <div class="val">${k.limit_usd != null ? usd2(k.limit_usd) : "none"}<small>${esc(k.reset_label || "")}</small></div>
        <div class="f">${esc(d.cap_help || "")}</div>
        <div class="warnset"><span>Warn at</span><b>${esc(d.warn_pct)}%</b><span class="f">your setting · default 75%</span>
          <button class="btn xs" type="button" data-orb="warn">Change…</button></div>
      </div>
      <div class="lim runner">
        <div class="top">${ic("lock")}<b>Clippa run budget</b><span class="ro">Per run</span></div>
        <div class="val"><code>${esc(d.run_budget?.flag || "--budget")}</code><small>set on each run</small></div>
        <div class="f">${esc(d.run_budget?.text || "")}</div>
      </div>
    </div>
    <div class="orb-col acct">
      <div class="sub">Account</div>
      <div class="bal"><span class="big">${usd2(d.account?.balance_usd)}</span> <span>${d.account?.bought_usd != null ? `credit balance, of ${usd2(d.account.bought_usd)} bought` : "credit balance"}</span></div>
      <div><a class="ext" href="${esc(href)}" target="_blank" rel="noopener">Top up on OpenRouter ${ic("external")}</a>
        <div class="f" style="margin-top:3px">${esc(d.topup || "")}</div></div>
      <div class="mk">${ic("key")}<div><b>${esc(mgmt.text || "Management key")}</b><div class="f">${esc(mgmt.detail || "")}</div></div></div>
      <div class="danger">${powerBtn}<span class="f">${k.disabled ? "Enable it to let Clippa call models again." : "Every Clippa model call fails until it's enabled again."}</span></div>
    </div>
  </div>`;
}
function eventsHtml(d) {
  const rows = d.events || [];
  if (!rows.length) return "";
  return `<div class="orb-ev"><div class="sub">Limit events</div>
    ${rows.map(e => `<div class="ev"><span class="w">${esc(e.when)}</span><span class="k ${esc(e.tone)}">${esc(e.title)}</span><span>${esc(e.text)}${e.code ? ` <code>${esc(e.code)}</code>` : ""}</span></div>`).join("")}
  </div>`;
}

function openOrb(html) {
  const d = $("#tool");
  d.dataset.kind = "openrouter";
  d.classList.add("orbd");
  d.innerHTML = html;
  if (!d.open) d.showModal();
  d.querySelector("input, select, button")?.focus();
}
function head(title) {
  return `<div class="m-head"><b>${esc(title)}</b><button class="x" type="button" aria-label="Close" title="Close (Esc)" data-close>${ic("close")}</button></div>`;
}
function diffTable(rows) {
  return `<table class="diff"><tr><th></th><th>Now</th><th></th><th>After</th></tr>${(rows || []).map(r =>
    `<tr><td>${esc(r.label)}</td><td class="old">${esc(r.now)}</td><td class="arr">→</td><td class="new ${r.changed ? "chg" : ""} ${r.bad ? "bad" : ""}">${esc(r.after)}${r.note ? `<span class="same">${esc(r.note)}</span>` : ""}</td></tr>`).join("")}</table>`;
}
function reqBlock(req) {
  const path = esc(req?.path || "").replace(/\{[^}]+\}/, m => `<b>${m}</b>`);
  return `<div class="req">${esc(req?.method || "PATCH")} ${path}\n${esc(JSON.stringify(req?.body || {}))}</div>`;
}

function limitDialog() {
  if (!ORB?.can_edit) return toast(ORB?.edit_why || "Set a management key to change the limit.", true);
  const k = ORB.key || {}, form = ORB_FORM || {};
  const reset = form.reset || k.limit_reset || "monthly";
  const limit = form.limit != null && form.limit !== "" ? form.limit : (k.limit_usd ?? "");
  const opt = (v, label) => `<option value="${v}" ${reset === v ? "selected" : ""}>${label}</option>`;
  openOrb(`${head("Change the clippa key's limit")}<div class="tbody">
    <div class="steps"><span class="s on"><i>1</i>New limit</span><span class="ln"></span><span class="s"><i>2</i>Confirm</span></div>
    <div class="flds">
      <label class="fld">Spending limit, in dollars<input id="orbLimit" inputmode="decimal" value="${esc(limit)}"></label>
      <label class="fld">Resets<select id="orbReset">${opt("daily", "daily")}${opt("weekly", "weekly")}${opt("monthly", "monthly")}${opt("none", "does not reset")}</select></label>
    </div>
    <div class="dacts"><span class="f">Nothing is sent until you confirm.</span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="button" data-orb="limit-next">Review change</button></div>
  </div>`);
}
async function limitNext() {
  const limit = $("#orbLimit")?.value?.trim();
  const reset = $("#orbReset")?.value || "monthly";
  ORB_FORM = { limit, reset };
  let prev;
  try { prev = await api("/api/openrouter/limit", { limit: limit === "" ? null : +limit, limit_reset: reset }); }
  catch (e) { return toast(e.message, true); }
  const p = prev.preview;
  openOrb(`${head(p.title)}<div class="tbody">
    <div class="steps"><span class="s done"><i>${ic("check")}</i>New limit</span><span class="ln"></span><span class="s on"><i>2</i>Confirm</span></div>
    ${diffTable(p.rows)}${reqBlock(p.request)}
    <div class="dacts"><span class="f">${esc(p.note || "")}</span><button class="btn" type="button" data-orb="limit-back">Back</button>
      <button class="btn primary" type="button" data-orb="limit-confirm" ${p.unchanged ? "disabled" : ""}>${esc(p.confirm_label)}</button></div>
  </div>`);
}
async function limitConfirm() {
  const form = ORB_FORM || {};
  try { ORB = await api("/api/openrouter/limit", { limit: +form.limit, limit_reset: form.reset, confirm: true }); }
  catch (e) { return toast(e.message, true); }
  ORB_FORM = null;
  $("#tool").close();
  applyOpenRouter(ORB);
  toast("Limit updated");
}

function warnDialog() {
  openOrb(`${head("Warn before the cap")}<div class="tbody">
    <label class="fld">Warn when this much of the cap is used
      <input id="orbWarn" inputmode="numeric" value="${esc(ORB?.warn_pct ?? 75)}"></label>
    <div class="f">A whole percent from 1 to 99. The default is 75. The mark on the cap meter moves with it.</div>
    <div class="dacts"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="button" data-orb="warn-save">Save</button></div>
  </div>`);
}
async function saveWarn() {
  const pct = $("#orbWarn")?.value?.trim();
  let saved;
  try { saved = await api("/api/openrouter/warn", { pct: +pct }); }
  catch (e) { return toast(e.message, true); }
  $("#tool").close();
  applyOpenRouter(saved);
}

function disableDialog(off) {
  if (!ORB?.can_edit) return toast(ORB?.edit_why || "Set a management key to change the key.", true);
  api("/api/openrouter/disabled", { disabled: off }).then(prev => {
    const p = prev.preview;
    openOrb(`${head(p.title)}<div class="tbody">
      <div class="callout ${esc(p.callout_tone || "info")}">${ic("warn")}<span>${esc(p.callout)}</span></div>
      ${diffTable(p.rows)}${reqBlock(p.request)}
      <div class="dacts"><span class="f">${esc(p.note || "")}</span><button class="btn" type="button" data-close>Cancel</button>
        <button class="btn ${p.danger ? "bad solid" : "primary"}" type="button" data-orb="disable-confirm" data-off="${off ? "1" : "0"}" ${p.unchanged ? "disabled" : ""}>${p.danger ? ic("power") : ""}${esc(p.confirm_label)}</button></div>
    </div>`);
  }).catch(e => toast(e.message, true));
}
async function disableConfirm(btn) {
  const off = btn?.dataset?.off === "1";
  let saved;
  try { saved = await api("/api/openrouter/disabled", { disabled: off, confirm: true }); }
  catch (e) { return toast(e.message, true); }
  $("#tool").close();
  applyOpenRouter(saved);
  toast(off ? "Key disabled" : "Key enabled");
}

document.addEventListener("click", e => {
  const t = e.target.closest("[data-orb]");
  if (!t || t.disabled) return;
  const a = t.dataset.orb;
  if (a === "refresh") return refreshBudget(true);
  if (a === "limit") return limitDialog();
  if (a === "limit-next") return limitNext();
  if (a === "limit-back") return limitDialog();
  if (a === "limit-confirm") return limitConfirm();
  if (a === "warn") return warnDialog();
  if (a === "warn-save") return saveWarn();
  if (a === "disable") return disableDialog(true);
  if (a === "enable") return disableDialog(false);
  if (a === "disable-confirm") return disableConfirm(t);
});
$("#tool").addEventListener("close", () => {
  if ($("#tool").dataset.kind === "openrouter") { $("#tool").classList.remove("orbd"); $("#tool").dataset.kind = ""; }
});
setInterval(() => {
  const el = $("#orbRetryN");
  if (!el) return;
  const s = retryLeft();
  el.textContent = s + " s";
  if (s === 0 && ORB?.retry && !ORB._retryDone) { ORB._retryDone = true; refreshBudget(true); }
}, 1000);
