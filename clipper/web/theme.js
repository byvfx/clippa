// The look (#158): one stylesheet in web/themes/, worn by the war room and the Clippa chat alike.
// Loaded in <head> right after <link id="theme">, so a browser's pick is on before the first paint.
// No look is known here by name: a look is its file, and the custom properties in it.
//  - A pick is kept per browser ("clippa-theme" in localStorage); the browser's other tabs follow it.
//    One kept under a page's old key (data-old-key) moves to it on the first visit to either page.
//  - A page docked in another (the war room's chat frame) wears the outer page's look. It never
//    keeps a pick itself: it asks the outer page to, and the outer page tells it of each change.
//  - The page's own default is its <html data-theme>, or the one it passes to settle().
"use strict";
const ClippaTheme = (() => {
  const KEY = "clippa-theme";
  const OLD = document.currentScript?.dataset.oldKey || "";   // a key this page kept its pick in before
  const NAME = /^[\w-]+$/;
  const root = document.documentElement;
  const link = document.getElementById("theme");
  const dir = link.getAttribute("href").replace(/[^/]*$/, "");   // "/themes/" or "themes/"
  const HOME = root.dataset.theme || "";
  const outer = window.parent !== window ? window.parent : null;
  let picked = false;   // the look is a pick (this browser's, or the outer page's), not the page's default

  function read(k) { try { return (k && localStorage.getItem(k)) || ""; } catch { return ""; } }
  function outerLook() { try { return outer?.document.documentElement.dataset.theme || ""; } catch { return ""; } }
  function current() { return root.dataset.theme || ""; }

  // Once a look's file is in: the phone's browser bar takes its desk colour, and the page hears of it
  // (the chat's window titles come from the look's --label-* properties).
  function painted() {
    const desk = getComputedStyle(root).getPropertyValue("--desk").trim();
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && desk && !desk.startsWith("var(")) meta.setAttribute("content", desk);
    document.dispatchEvent(new Event("clippa-theme"));
  }
  link.addEventListener("load", painted);

  function use(name) {
    if (!NAME.test(name || "")) return;
    root.dataset.theme = name;
    const href = `${dir}${name}.css`;
    if (link.getAttribute("href") !== href) link.setAttribute("href", href);   // its load event paints
    else if (link.sheet) painted();
  }
  function tellFrames(name) {
    for (const f of document.querySelectorAll("iframe")) {
      try { f.contentWindow.postMessage({ clippa: "theme", name }, location.origin); } catch { /* not ours, or gone */ }
    }
  }
  // The viewer chose a look: keep it for this browser and show it here and in the frames docked here.
  function pick(name) {
    if (!NAME.test(name || "")) return;
    if (outerLook()) { outer.postMessage({ clippa: "pick", name }, location.origin); return; }
    picked = true;
    try { localStorage.setItem(KEY, name); if (OLD) localStorage.removeItem(OLD); } catch { /* storage off: this visit only */ }
    use(name);
    tellFrames(name);
  }
  // When the page knows which looks serve has: a pick that is gone, or no pick, wears the default.
  function settle(list, dflt = HOME) {
    if (outerLook()) return;   // docked: the outer page settles it and tells this one
    if (picked && list.includes(current())) return;
    const name = list.includes(dflt) ? dflt : list[0];
    use(name);
    tellFrames(name);
  }

  addEventListener("message", e => {
    if (e.origin !== location.origin || !NAME.test(e.data?.name || "")) return;
    if (e.data.clippa === "theme" && e.source === outer) { picked = true; use(e.data.name); }
    else if (e.data.clippa === "pick" && [...document.querySelectorAll("iframe")].some(f => f.contentWindow === e.source)) pick(e.data.name);
  });
  addEventListener("storage", e => {   // a pick in another tab of this browser
    if (e.key !== KEY || outerLook() || !NAME.test(e.newValue || "")) return;
    picked = true;
    use(e.newValue);
    tellFrames(e.newValue);
  });

  // a pick kept under the old key becomes the shared one, so the war room and the dock wear it too
  if (!outerLook() && !read(KEY) && NAME.test(read(OLD))) {
    try { localStorage.setItem(KEY, read(OLD)); localStorage.removeItem(OLD); } catch { /* storage off */ }
  }
  const first = outerLook() || read(KEY) || read(OLD);
  if (NAME.test(first)) { picked = true; use(first); }
  return { current, pick, settle };
})();
