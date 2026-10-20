"""Favorites and channel numbers on a playlist's page (kia.channels, through kia.api).

On the secure page, with editing on, each channel gets a star, and on the full playlist's
page (Everything) its number can be changed. Each change is saved straight away and the
playlists are rewritten; a number change reloads the page, because other channels may
move. Elsewhere the script only marks favorites.
"""

STYLE = """
/* Favorites and numbers; TV and radio under their own headings */
#channels tr.group-head th { font-size: var(--step-0); font-weight: 600; color: var(--fg); text-align: left; padding-top: var(--space-3); }
#channels .fav { width: 2rem; text-align: center; padding-right: 0; }
#channels .fav button { min-height: 32px; min-width: 32px; padding: 0; border: none; background: none; font-size: 1.15rem;
  color: var(--muted); cursor: pointer; line-height: 1; }
#channels .fav button[aria-pressed=true] { color: var(--warn); }
#channels .fav span { color: var(--warn); }
#channels td.test button { min-height: 32px; padding: .1rem .6rem; font-size: var(--step--1); }
#channels tr.test-result td { padding-top: 0; }
#channels tr.test-result p { margin: 0 0 var(--space-1); }
#channels td.num button { min-height: 32px; padding: 0 .4rem; border: 1px dashed var(--line); background: none; color: var(--fg);
  font: inherit; font-variant-numeric: tabular-nums; cursor: pointer; }
#channels td.num input { width: 5.5rem; min-height: 32px; font: inherit; padding: 0 .3rem; }
.chan-tools { display: grid; gap: var(--space-2); margin: var(--space-2) 0; }
.chan-tools label { display: grid; gap: .25rem; max-width: 22rem; }
.chan-tools input { min-height: 40px; font: inherit; padding: .4rem .6rem; border: 1px solid var(--line); border-radius: var(--radius-small);
  background: var(--card); color: var(--fg); }
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("whats-on"), table = document.getElementById("channels");
  if (!card || !table) return;
  var root = card.dataset.root || "";
  var everything = !document.querySelector("#playlists [data-profile]");
  var KEY = "kia-password";
  var pass = null, msg = null;

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "on") Object.keys(attrs.on).forEach(function (ev) { e.addEventListener(ev, attrs.on[ev]); });
      else if (attrs[k] === true) e.setAttribute(k, "");
      else if (attrs[k] !== false && attrs[k] != null) e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function getPassword() { try { return sessionStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function setPassword(v) { try { if (v) sessionStorage.setItem(KEY, v); else sessionStorage.removeItem(KEY); } catch (e) {} }
  function say(text, bad) { msg.hidden = !text; msg.className = bad ? "error" : "preview"; msg.textContent = text || ""; }
  function send(body) {
    return fetch(root + "api/channel", { method: "PUT", headers: { "Content-Type": "application/json", "X-Kia-Password": pass.value },
      body: JSON.stringify(body) }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function post(path, body) {
    return fetch(root + "api/" + path, { method: "POST", headers: { "Content-Type": "application/json", "X-Kia-Password": pass.value },
      body: JSON.stringify(body) }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function testRow(row, key, button) {
    var old = row.nextElementSibling;
    if (old && old.classList.contains("test-result")) old.remove();
    var line = el("tr", { class: "test-result" }, [el("td", { colspan: "9" }, [el("p", { class: "muted", text: "Testing… this takes up to 20 seconds." })])]);
    row.after(line);
    button.disabled = true;
    post("channel/test", { key: key }).then(function (r) {
      button.disabled = false;
      var cell = line.firstChild;
      cell.textContent = "";
      if (r._status !== 200) { cell.appendChild(el("p", { class: "error", text: r.error || "Something went wrong." })); if (r._status === 401) { setPassword(""); pass.focus(); } return; }
      setPassword(pass.value);
      if (r.ok) {
        cell.appendChild(el("p", { class: "preview", text: "It plays from here (" + (r.height ? r.height + "p, " : "") + r.seconds + " s). If it won't play on a device, " +
          "the problem may be that device or the app; try it in another player." }));
        return;
      }
      cell.appendChild(el("p", { class: "error", text: "It didn't play: " + (r.error || "no picture or sound") + "." }));
      cell.appendChild(el("button", { type: "button", text: "Remove it until it works", on: { click: function () {
        post("channel/remove", { key: key }).then(function (res) { if (res._status !== 200) return failed(res); location.reload(); });
      } } }));
    }, function () { button.disabled = false; line.firstChild.textContent = "kia didn't answer. Is it running?"; });
  }
  function failed(r) {
    say(r.error || "Something went wrong.", true);
    if (r._status === 401) { setPassword(""); pass.focus(); }
  }

  Promise.all([
    fetch(root + "api/profiles").then(function (r) { return r.ok ? r.json() : null; }),
    fetch(root + "api/channel-choices").then(function (r) { return r.ok ? r.json() : null; })
  ]).then(function (res) {
    var info = res[0], choices = res[1];
    if (!choices) return;
    var favorites = {};
    choices.favorites.forEach(function (k) { favorites[k] = true; });
    var can = info && info.editable && info.secure;
    var head = table.querySelector("thead tr");
    head.insertBefore(el("th", { class: "fav", "aria-label": "Favorite" }), head.querySelector("th.name"));
    if (can) {
      pass = el("input", { type: "password", id: "ch-pass", autocomplete: "current-password", value: getPassword() });
      msg = el("p", { role: "status", hidden: true });
      var tools = el("div", { class: "chan-tools" }, [
        el("p", { class: "muted", text: "Star a channel to make it a favorite: favorites come first in every playlist and get their own Favorites category." +
          (everything ? " Choose a number to give a channel that number everywhere; a channel already on it moves." : "") }),
        el("label", { for: "ch-pass" }, [el("strong", { text: "Password" }), pass]), msg]);
      if (everything) tools.appendChild(el("div", { class: "actions" }, [el("button", { type: "button", text: "Renumber everything",
        on: { click: function () {
          if (!confirm("Number every channel 1, 2, 3 down this list (TV and radio apart), keeping the numbers you chose? " +
            "Channel numbers change on every device. Afterwards, clear Kodi's TV data on each one (Settings > PVR & Live TV > General > Clear data).")) return;
          fetch(root + "api/renumber", { method: "POST", headers: { "Content-Type": "application/json", "X-Kia-Password": pass.value } })
            .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); })
            .then(function (r) { if (r._status !== 200) return failed(r); setPassword(pass.value); location.reload(); },
              function () { say("kia didn't answer. Is it running?", true); });
        } } })]));
      var removed = Object.keys(choices.removed || {});
      if (everything && removed.length) {
        var list = el("ul", { class: "links" });
        removed.forEach(function (k) {
          var r = choices.removed[k];
          list.appendChild(el("li", {}, [el("strong", { text: r.name }), el("span", { text: " removed " + new Date(r.when).toLocaleDateString() + " " }),
            el("button", { type: "button", text: "Put back", on: { click: function () {
              post("channel/restore", { key: k }).then(function (res) { if (res._status !== 200) return failed(res); setPassword(pass.value); location.reload(); });
            } } })]));
        });
        tools.appendChild(el("div", {}, [el("h3", { text: "Removed because they didn't play" }),
          el("p", { class: "muted", text: "The nightly check puts each one back when its stream works again." }), list]));
      }
      table.closest(".wide").before(tools);
      head.appendChild(el("th", { class: "test", "aria-label": "Test" }));
    }
    table.querySelectorAll("tbody tr:not(.group-head)").forEach(function (row) {
      var key = row.dataset.key, cell = el("td", { class: "fav" });
      row.insertBefore(cell, row.querySelector("td.name"));
      if (!can) { if (favorites[key]) cell.appendChild(el("span", { text: "★", title: "Favorite" })); return; }
      var star = el("button", { type: "button", "aria-pressed": favorites[key] ? "true" : "false", text: favorites[key] ? "★" : "☆",
        "aria-label": "Favorite: " + row.querySelector("td.name").textContent, on: { click: function () {
          var on = star.getAttribute("aria-pressed") !== "true";
          star.disabled = true;
          send({ key: key, favorite: on }).then(function (r) {
            star.disabled = false;
            if (r._status !== 200) return failed(r);
            setPassword(pass.value);
            star.setAttribute("aria-pressed", on ? "true" : "false");
            star.textContent = on ? "★" : "☆";
            say(on ? "Added to favorites. It's at the top of your playlists now." : "Removed from favorites.", false);
          }, function () { star.disabled = false; say("kia didn't answer. Is it running?", true); });
        } } });
      cell.appendChild(star);
      var test = el("button", { type: "button", class: "test", text: "Test", "aria-label": "Test " + row.querySelector("td.name").textContent,
        on: { click: function () { testRow(row, key, test); } } });
      row.appendChild(el("td", { class: "test" }, [test]));
      if (!everything) return;
      var num = row.querySelector("td.num"), current = num.textContent;
      var edit = el("button", { type: "button", text: current || "–", title: "Change this channel's number", on: { click: function () {
        var box = el("input", { type: "number", min: "1", max: "99999", value: current, "aria-label": "Channel number" });
        var finished = false;
        function done(ok) {
          if (finished) return;
          finished = true;
          if (!ok) { num.textContent = ""; num.appendChild(edit); return; }
          var v = box.value.trim();
          send({ key: key, number: v ? parseInt(v, 10) : null }).then(function (r) {
            if (r._status !== 200) { failed(r); num.textContent = ""; num.appendChild(edit); return; }
            setPassword(pass.value);
            location.reload();
          }, function () { say("kia didn't answer. Is it running?", true); });
        }
        box.addEventListener("keydown", function (ev) { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); });
        box.addEventListener("blur", function () { done(box.value.trim() !== current); });
        num.textContent = ""; num.appendChild(box); box.focus(); box.select();
      } } });
      num.textContent = ""; num.appendChild(edit);
    });
  }).catch(function () {});
})();
</script>
"""
