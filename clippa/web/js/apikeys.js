// War room: API keys (#281). Serve's own keys: where each comes from, what uses it, and the last
// answer from its provider; Check asks again (one free request each). The page never sees a value.

let AK = null;  // {data, busy: name | "all" | ""}
let AKWIN = 0;
const akWin = win => win === AKWIN && $("#tool").open && $("#tool").dataset.kind === "apikeys";

async function showApiKeys() {
  const d = $("#tool");
  const win = ++AKWIN;
  d.dataset.kind = "apikeys"; d.classList.remove("cover"); d.classList.add("facesw");
  d.innerHTML = `<div class="m-head"><b>Keys</b><button class="x" aria-label="Close" title="Close (Esc)" data-close>${ic("close")}</button></div><div class="tbody">Loading…</div>`;
  if (!d.open) d.showModal();
  try {
    const data = await api("/api/keys");
    if (!akWin(win)) return;
    AK = { data, busy: "", win };
    renderApiKeys();
  } catch (e) {
    const box = d.querySelector(".tbody");
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  }
}
function renderApiKeys() {
  const { data, busy } = AK;
  const state = r => {
    if (busy === "all" || busy === r.name) return `<span class="by">checking…</span>`;
    if (!r.set) return `<span class="by">not set</span>`;
    const l = r.last;
    if (!l) return `<span class="by">${r.checkable ? "not checked yet" : "no check"}</span>`;
    const cls = l.ok === true ? "ok" : l.ok === false ? "bad" : "mid";
    return `<span class="${cls}">${l.ok === true ? ic("check") : l.ok === false ? ic("close") : "?"}${esc(l.note)}</span> <span class="by">${ago(l.at)}</span>`;
  };
  const row = r => `<tr class="${r.set ? "" : "off"}">
      <td><b>${esc(r.name)}</b><div class="by">${esc(r.what)}</div><code class="akenv-sm">${esc(r.env)}${r.set ? " · " + esc(r.source.startsWith("$") ? "env" : r.source) : ""}</code></td>
      <td class="akenv"><code>${esc(r.env)}</code></td>
      <td class="akenv">${r.set ? esc(r.source) : `<span class="by">—</span>`}</td>
      <td>${state(r)}</td>
      <td>${r.set && r.checkable ? `<button class="btn" data-akcheck="${esc(r.name)}"${busy ? " disabled" : ""}>Check</button>` : ""}</td>
    </tr>`;
  const held = data.held.map(h => `<div class="nst"><div class="nh"><b>${esc(h.stream)}</b><span class="by">held since ${ago(h.since)}</span></div><div class="by">${esc(h.error)}</div></div>`).join("");
  $("#tool").innerHTML = `<div class="m-head"><b>Keys</b><span style="color:var(--faint);font-size:12px">as the war room's server on ${esc(data.host)} sees them (on the hub: serve's, the bot's and the worker's) · values never leave it</span><button class="x" aria-label="Close" title="Close (Esc)" data-close>${ic("close")}</button></div>
    <div class="news apikeys">
      ${data.held.length ? `<div class="f" style="color:var(--red)">Held for a rejected key: the worker retries these by itself once the key changes</div>${held}` : ""}
      <div class="nbar"><button class="btn primary" id="akAll"${busy ? " disabled" : ""}>Check all</button><span class="by">one free request a provider; Brave, Pixabay, DVIDS and sync.so aren't asked</span></div>
      <div class="akwrap"><table class="aktable"><thead><tr><th>Key</th><th class="akenv">Env var</th><th class="akenv">From</th><th>Last check</th><th></th></tr></thead>
        <tbody>${data.keys.map(row).join("")}</tbody></table></div>
      <div class="by">To change one on the hub: <code>deploy/hub/env.local</code> on the laptop (or its file in <code>secrets/</code>), then <code>deploy/hub/deploy.sh</code>, which checks it against the laptop's own first.</div>
    </div>`;
}
async function apiKeysCheck(name) {
  const { win } = AK;
  AK.busy = name || "all"; renderApiKeys();
  try {
    const data = await api("/api/keys/check", name ? { name } : {});
    if (!AK || !akWin(win)) return;
    AK.data = data;
    const bad = data.keys.filter(r => r.last?.ok === false && (!name || r.name === name));
    toast(bad.length ? `Rejected: ${bad.map(r => r.name).join(", ")}` : name ? `${name}: ${data.keys.find(r => r.name === name)?.last?.note || "checked"}` : "Checked", bad.length > 0);
  } catch (e) { toast(e.message, true); }
  if (AK && akWin(win)) { AK.busy = ""; renderApiKeys(); }
}

document.addEventListener("click", e => {
  const t = e.target.closest?.("#apikeysBtn,#akAll,[data-akcheck]");
  if (!t) return;
  if (t.id === "apikeysBtn") return showApiKeys();
  if (!AK || AK.busy) return;
  if (t.id === "akAll") return apiKeysCheck("");
  if (t.dataset.akcheck) return apiKeysCheck(t.dataset.akcheck);
});
$("#tool").addEventListener("close", () => { if ($("#tool").dataset.kind === "apikeys") { AK = null; AKWIN++; } });
