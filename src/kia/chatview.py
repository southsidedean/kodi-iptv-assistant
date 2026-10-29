"""The Chat page (chat.html): a plain chat box over kia's tools (0.8.4).

A household member asks about their TV setup and kia answers, through the gateway's AI route.
The page keeps a short history in the browser (localStorage) and sends only the recent turns, so
kia stores nothing. It reads the answer in with textContent, never as HTML, because the reply and
the tool data it quotes come partly from third-party playlists and guide sites. The server side
is kia.api.chat_reply and kia.chat; this is only the page.

With chat actions on (KIA_CHAT_ACTIONS, 0.8.5), kia can ask to do a few things too. The server
says so on load, so the intro matches, and an answer that leaves a request shows a note under it
with a link to approve it on the Dashboard. The note text is built the same careful way as a
reply, with textContent, since kia's words for a request can quote a playlist or guide.
"""

from __future__ import annotations

STYLE = r"""
#chat-page .log { display: flex; flex-direction: column; gap: .6rem; margin: 1rem 0; min-height: 6rem; }
#chat-page .msg { padding: .5rem .75rem; border-radius: .7rem; max-width: 44rem; white-space: pre-wrap;
                  line-height: 1.45; overflow-wrap: anywhere; }
#chat-page .you { align-self: flex-end; background: #2b6cb0; color: #fff; }
#chat-page .kia { align-self: flex-start; background: #eef1f5; color: #1a1d22; }
#chat-page .tools { align-self: flex-start; font-size: .8rem; color: #667; margin-top: -.35rem; }
#chat-page .asked { align-self: flex-start; max-width: 44rem; font-size: .85rem; margin-top: -.2rem;
                    background: #fff6e5; color: #5a4a2a; border: 1px solid #e6d8b8;
                    border-radius: .6rem; padding: .4rem .7rem; overflow-wrap: anywhere; }
#chat-page .asked a { color: inherit; font-weight: 600; }
#chat-page form { display: flex; gap: .5rem; align-items: flex-end; }
#chat-page textarea { flex: 1; min-height: 2.6rem; max-height: 12rem; resize: vertical; font: inherit; padding: .5rem; }
#chat-page .opts { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin: .5rem 0; }
#chat-page .note { color: #667; }
#chat-page .pw { margin: .5rem 0; }
.one-login #chat-page .pw { display: none; }
@media (prefers-color-scheme: dark) {
  #chat-page .kia { background: #20242b; color: #e6e8ec; }
  #chat-page .tools, #chat-page .note { color: #99a; }
  #chat-page .asked { background: #2a2720; color: #e8dcc0; border-color: #4a452f; }
}
"""

SCRIPT = r"""
<script>
(function () {
  var page = document.getElementById("chat-page");
  if (!page || location.protocol === "file:") return;
  var log = page.querySelector(".log");
  var form = page.querySelector("form");
  var box = page.querySelector("textarea");
  var send = page.querySelector(".send");
  var heavy = page.querySelector(".heavy");
  var note = page.querySelector(".note");
  var intro = page.querySelector(".intro");
  var pw = page.querySelector(".pw input");
  var HIST = "kia-chat-history", PASS = "kia-password", MAX = 20;
  var history = [];
  // Shown instead of the read-only intro when chat actions are on (KIA_CHAT_ACTIONS). The server
  // tells us on load whether they are, so we never promise what the chat can't do.
  var ACTIONS_INTRO = "Ask about your channels, the guide, or what's on, in plain words. kia answers "
    + "from its own tools, and it can ask to do a few things for you (refresh the guide, test a "
    + "channel, add a channel to Plex, check a stack choice). It never changes anything on its own: "
    + "each of those waits for you to approve it on the Dashboard.";

  function load() { try { history = JSON.parse(localStorage.getItem(HIST) || "[]"); } catch (e) { history = []; }
    if (!Array.isArray(history)) history = []; }
  function save() { try { localStorage.setItem(HIST, JSON.stringify(history.slice(-MAX))); } catch (e) {} }
  function password() { try { return sessionStorage.getItem(PASS) || ""; } catch (e) { return ""; } }

  function bubble(role, text, tools) {
    var who = role === "user" ? "you" : "kia";
    var e = document.createElement("div");
    e.className = "msg " + who;
    e.textContent = text;  // never innerHTML: the reply and its tool data are partly third-party
    log.appendChild(e);
    if (tools && tools.length) {
      var t = document.createElement("div");
      t.className = "tools";
      t.textContent = "looked at: " + tools.join(", ");
      log.appendChild(t);
    }
    log.scrollTop = log.scrollHeight;
  }
  function draw() { log.textContent = ""; history.forEach(function (m) { bubble(m.role, m.content); }); }
  function say(text) { note.textContent = text || ""; }

  // A request an acting tool left this answer: what kia wants to do, and where to approve it. kia's
  // words for it (req.what) can quote playlist or guide text, so every piece goes in with textContent,
  // and the only link is the approve_at the server gave, used solely when it's a Dashboard https URL.
  function requestNote(req) {
    var what = req.what || "do something", state = req.state || "waiting";
    var e = document.createElement("div");
    e.className = "asked";
    if (state === "waiting") {
      e.appendChild(document.createTextNode("kia asked to " + what + ". "));
      var at = req.approve_at || "";
      if (at.indexOf("https://") === 0) {
        var link = document.createElement("a");
        link.href = at; link.textContent = "Approve it on the Dashboard";
        e.appendChild(link);
        e.appendChild(document.createTextNode("."));
      } else {
        e.appendChild(document.createTextNode("Approve it on " + (at || "the Dashboard") + "."));
      }
    } else if (state === "running") {
      e.textContent = "kia is doing that now: " + what + ". The Dashboard shows how it goes.";
    } else if (state === "done") {
      e.textContent = "kia did that: " + what + ".";
    } else if (state === "failed") {
      e.textContent = "kia tried to " + what + ", but it didn't finish. The Dashboard has the details.";
    } else {
      e.textContent = "kia asked to " + what + " (" + state + ").";
    }
    log.appendChild(e);
    log.scrollTop = log.scrollHeight;
  }

  function headers() {
    var h = { "Content-Type": "application/json" };
    var p = pw && pw.value ? pw.value : password();
    if (p) h["X-Kia-Password"] = p;
    return h;
  }
  function busy(on) { send.disabled = on; box.disabled = on; }

  function ask(text) {
    history.push({ role: "user", content: text });
    save(); bubble("user", text); say("kia is thinking...");
    busy(true);
    var recent = history.slice(-MAX);
    fetch("api/chat", { method: "POST", headers: headers(), cache: "no-cache",
      body: JSON.stringify({ messages: recent, model: heavy && heavy.checked ? "heavy" : "default" }) })
      .then(function (r) {
        if (r.status === 401) { say("Enter kia's password above, then ask again."); return null; }
        return r.json().catch(function () { return null; });
      })
      .then(function (d) {
        busy(false);
        if (!d) { if (!note.textContent) say("kia couldn't answer just now. Try again."); return; }
        if (d.available === false) { say(d.reply || "kia's chat isn't on."); return; }
        if (d.error) { say(d.error); return; }
        say("");
        var reply = d.reply || "(kia had no answer for that.)";
        history.push({ role: "assistant", content: reply });
        save(); bubble("assistant", reply, d.tools_used);
        if (d.requests && d.requests.length) d.requests.forEach(requestNote);
        if (d.over_budget) say("That used the last of the household's chat allowance for now.");
      })
      .catch(function () { busy(false); say("kia couldn't answer just now. Try again."); });
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = box.value.trim();
    if (!text) return;
    box.value = "";
    ask(text);
  });
  // Enter sends, Shift+Enter makes a new line.
  box.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
  });
  page.querySelector(".clear").addEventListener("click", function () {
    history = []; save(); draw(); say("");
  });

  load(); draw();
  // Say up front whether the chat is on, without sending a question (no model call, no cost).
  fetch("api/chat", { headers: headers(), cache: "no-cache" })
    .then(function (r) { return r.status === 401 ? { need: true } : r.json().catch(function () { return null; }); })
    .then(function (d) {
      if (!d) return;
      if (d.need) { say("Enter kia's password above to use the chat."); return; }
      if (d.available === false) { say(d.reply || "kia's chat isn't on."); busy(true); box.disabled = false; send.disabled = true; return; }
      if (d.actions && intro) intro.textContent = ACTIONS_INTRO;
    })
    .catch(function () {});
})();
</script>
"""


def body() -> str:
    """The Chat page's markup. The script above drives it; the password row is hidden for a
    front-door admin (the one-login class), as on the other pages."""
    return (
        '<main id="chat-page">'
        "<h1>Ask kia</h1>"
        '<p class="intro">Ask about your channels, the guide, or what\'s on, in plain words. kia answers '
        "from its own tools. It can look things up and explain them; it can't change anything here.</p>"
        '<div class="pw"><label>kia\'s password '
        '<input type="password" autocomplete="current-password"></label></div>'
        '<div class="log" aria-live="polite"></div>'
        '<form><textarea placeholder="What football is on tonight?" rows="2"></textarea>'
        '<button type="submit" class="send">Ask</button></form>'
        '<div class="opts">'
        '<label><input type="checkbox" class="heavy"> Use the stronger model (costs more)</label>'
        '<button type="button" class="clear">Clear chat</button></div>'
        '<p class="note"></p>'
        "</main>"
    )
