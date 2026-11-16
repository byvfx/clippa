// The Clippa messenger (#158): before the first paint, the look this browser picked last time,
// so the page doesn't flash the default first. chat.js checks it against serve's list after.
"use strict";
(function () {
  let name = "";
  try { name = localStorage.getItem("clippa-chat-theme") || ""; } catch (e) { /* storage off: the default */ }
  if (/^[\w-]+$/.test(name)) {
    document.getElementById("theme").setAttribute("href", `/themes/${name}.css`);
    document.documentElement.dataset.theme = name;
  }
})();
