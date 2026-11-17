// War room: the Clippa chat (#158) at the side. The dock is the /chat page itself in a frame
// (#chatFrame), so the messenger keeps one code path and one store; this file only shows and
// hides it, and badges the replies that came while nobody looked. On a desktop the Chat button
// (or M) shows or hides it, remembered per browser; on a phone it is a view of its own (the Chat
// tab), so it never covers the review cards.

const CHAT_HIDDEN = "warroom.chat.hidden";
const chatFrame = () => $("#chatFrame");

function chatShown() {  // on screen now: the dock on a desktop, the Chat view on a phone
  return PHONE.matches ? document.body.dataset.view === "chat" : document.body.classList.contains("chat-on");
}
function chatTell(msg) { chatFrame()?.contentWindow?.postMessage({ clippa: "dock", ...msg }, location.origin); }
function chatSync() {  // after anything that shows or hides it
  const on = document.body.classList.contains("chat-on");
  if (PHONE.matches) document.body.style.setProperty("--views-h", `${$("#views").offsetHeight}px`);  // the Chat view stops at the tabs
  $("#chatBtn").classList.toggle("on", on);
  $("#chatBtn").setAttribute("aria-expanded", String(on));
  chatTell({ seen: chatShown() });
}
function chatShow(on, focus) {
  document.body.classList.toggle("chat-on", on);
  store.set(CHAT_HIDDEN, on ? null : "1");
  chatSync();
  if (on && focus) chatTell({ focus: true });
}
function toggleChat() {  // the header button and M
  if (PHONE.matches) setView(document.body.dataset.view === "chat" ? "queue" : "chat");
  else chatShow(!document.body.classList.contains("chat-on"), true);
}
function chatBadge(n) {
  for (const id of ["#chatN", "#vnChat"]) $(id).textContent = n ? ` ${n}` : "";
  $("#chatBtn").title = `The Clippa chat at the side: show or hide it (M)${n ? `. ${n} new from Clippa` : ""}`;
}

addEventListener("message", e => {
  const f = chatFrame();
  if (e.origin !== location.origin || !f || e.source !== f.contentWindow || e.data?.clippa !== "dock") return;
  if ("unseen" in e.data) chatBadge(+e.data.unseen || 0);
  if (e.data.hello) chatSync();   // it has just loaded: whether it's on screen
  if (e.data.leave) { if (PHONE.matches) setView("queue"); else $("#chatBtn").focus(); }   // Esc in its box
});
$("#chatBtn").onclick = toggleChat;
$("#chatHide").onclick = () => (PHONE.matches ? setView("queue") : chatShow(false));
PHONE.addEventListener("change", chatSync);
document.body.classList.toggle("chat-on", !store.get(CHAT_HIDDEN));
chatSync();
