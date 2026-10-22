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
#channels .fav button { min-height: 40px; min-width: 40px; padding: 0; border: none; background: none; font-size: 1.15rem;
  color: var(--muted); cursor: pointer; line-height: 1; }
#channels .fav button[aria-pressed=true] { color: var(--warn); }
#channels .fav span { color: var(--warn); }
#channels td.name button.test { min-height: 32px; margin-left: .5rem; padding: .1rem .6rem; font-size: var(--step--1); }
#channels tr.test-result td { padding-top: 0; }
#channels tr.test-result p { margin: 0 0 var(--space-1); }
#channels td.num button { min-height: 36px; padding: 0 .4rem; border: 1px dashed var(--muted); background: none; color: var(--fg);
  font: inherit; font-variant-numeric: tabular-nums; cursor: pointer; }
#channels td.num input { width: 5.5rem; min-height: 32px; font: inherit; padding: 0 .3rem; border: 1px solid var(--control); }
/* On a phone, the guide columns go so the channel and its buttons fit. */
@media (max-width: 34rem) { #channels td.next, #channels th.next { display: none; } }
.chan-tools { display: grid; gap: var(--space-2); margin: var(--space-2) 0; }
.chan-tools label { display: grid; gap: .25rem; max-width: 22rem; }
.chan-tools input { min-height: 40px; font: inherit; padding: .4rem .6rem; border: 1px solid var(--control); border-radius: var(--radius-small);
  background: var(--card); color: var(--fg); }
/* Touch screens: every control at least 44px, so it's easy to hit with a finger. */
@media (pointer: coarse) {
  #channels .fav button { min-width: 44px; min-height: 44px; }
  #channels td.name button.test, #channels td.num button, #channels td.num input, .chan-tools input { min-height: 44px; }
}
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("whats-on"), table = document.getElementById("channels");
  if (!card || !table) return;
  var root = card.dataset.root || "";
  var top = document.querySelector("#playlists .playlist");
  var everything = top && !top.hasAttribute("data-profile") && !top.hasAttribute("data-builtin");
  var KEY = "kia-password", FLASH = "kia-flash";
  var pass = null, msg = null;
  var DOWN = "kia didn't answer. Is it running?";

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
  // Read aloud from one region that's always in the page (a region that appears together
  // with its text often isn't read out).
  var announce = el("p", { class: "visually-hidden", role: "status" });
  document.body.appendChild(announce);
  function tell(text) { announce.textContent = ""; setTimeout(function () { announce.textContent = text; }, 50); }
  // A busy button ignores more clicks: each would start the work again.
  function busy(button, work) {
    if (button.getAttribute("aria-busy")) return;
    button.setAttribute("aria-busy", "true");  // stays focusable, unlike disabled
    var done = function () { button.removeAttribute("aria-busy"); };
    work().then(done, done);
  }
  // A message right under the row it's about (or, with no row, above the list).
  function rowSay(row, text, bad, extra) {
    if (text) tell(text);
    if (!row) { msg.hidden = !text; msg.className = bad ? "error" : "preview"; msg.textContent = text || ""; return; }
    var old = row.nextElementSibling;
    if (old && old.classList.contains("test-result")) old.remove();
    if (!text) return;
    var cell = el("td", { colspan: "9" }, [el("p", { class: bad ? "error" : "preview", text: text })]);
    (extra || []).forEach(function (x) { cell.appendChild(x); });
    var line = el("tr", { class: "test-result" }, [cell]);
    row.after(line);
    return cell;
  }
  // Remember a message across the reload a change needs, and show it by its row afterwards.
  function flash(key, text) { try { sessionStorage.setItem(FLASH, JSON.stringify({ key: key, text: text })); } catch (e) {} }
  function call(method, path, body) {
    return fetch(root + "api/" + path, { method: method, headers: { "Content-Type": "application/json", "X-Kia-Password": pass.value },
      body: body ? JSON.stringify(body) : undefined }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function failed(row, r) {
    rowSay(row, r.error || "Something went wrong.", true);
    if (r._status === 401) { setPassword(""); pass.focus(); pass.scrollIntoView({ block: "center" }); }
  }
  function plain(error) {
    var e = String(error || "");
    if (/404|not found/i.test(e)) return "the stream's address doesn't exist any more";
    if (/403|forbidden/i.test(e)) return "the stream's server turned kia away (it may only play in some countries)";
    if (/timed out|timeout/i.test(e)) return "the stream didn't answer in time";
    if (/resolve|name/i.test(e)) return "the stream's server name couldn't be found";
    if (/refused|unreachable/i.test(e)) return "the stream's server wasn't reachable";
    return e || "no picture or sound";
  }
  function name(row) { return row.querySelector("td.name").firstChild.textContent; }

  function testRow(row, key, button) {
    busy(button, function () { return runTest(row, key); });
  }
  function runTest(row, key) {
    rowSay(row, "Testing… this takes up to 20 seconds.");
    return call("POST", "channel/test", { key: key }).then(function (r) {
      if (r._status !== 200) return failed(row, r);
      setPassword(pass.value);
      if (r.ok) return rowSay(row, "It plays from here (" + (r.height ? r.height + "p, " : "") + r.seconds + " s). If it won't play on a device, " +
        "the problem may be that device or the app; try it in another player.");
      var remove = el("button", { type: "button", text: "Take it out until it works", on: { click: function () {
        if (remove.getAttribute("aria-busy")) return;
        if (!confirm("Take " + name(row) + " out of every playlist? The nightly check puts it back after it works three nights in a row.")) return;
        busy(remove, function () {
          return call("POST", "channel/remove", { key: key }).then(function (res) {
            if (res._status !== 200) return failed(row, res);
            flash("", "Removed " + name(row) + " from every playlist. " + (everything ? "It's listed above, with Put back." : "To put it back, use Everything."));
            location.reload();
          }, function () { rowSay(row, DOWN, true); });
        });
      } } });
      rowSay(row, "It didn't play: " + plain(r.error) + ".", true, [remove]);
    }, function () { rowSay(row, DOWN, true); });
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
      msg = el("p", { hidden: true });
      var tools = el("div", { class: "chan-tools" }, [
        el("p", { class: "muted", text: "Star a channel to make it a favorite: favorites get their own Favorites group in your players. " +
          "Test tries a channel's stream now." +
          (everything ? " Choose a channel's number to change it everywhere; a channel already on that number moves to a free one." : "") }),
        el("label", { for: "ch-pass" }, [el("strong", { text: "Password" }), pass]), msg]);
      if (everything) tools.appendChild(el("div", { class: "actions" }, [el("button", { type: "button", text: "Renumber everything",
        on: { click: function (ev) {
          var button = ev.currentTarget;
          if (button.getAttribute("aria-busy")) return;
          if (!confirm("Number every channel 1, 2, 3 down this list (TV and radio apart), keeping the numbers you chose? " +
            "Channel numbers change on every device. Afterwards, clear Kodi's TV data on each one (Settings > PVR & Live TV > General > Clear data).")) return;
          busy(button, function () {
            return call("POST", "renumber").then(function (r) { if (r._status !== 200) return failed(null, r); setPassword(pass.value);
              flash("", "Renumbered. Now clear Kodi's TV data on each device so it picks up the new numbers."); location.reload(); },
              function () { rowSay(null, DOWN, true); });
          });
        } } })]));
      var removed = Object.keys(choices.removed || {});
      if (everything && removed.length) {
        var list = el("ul", { class: "links" });
        removed.forEach(function (k) {
          var r = choices.removed[k];
          list.appendChild(el("li", {}, [el("strong", { text: r.name }), el("span", { text: " removed " + new Date(r.when).toLocaleDateString() + " " }),
            el("button", { type: "button", text: "Put back", "aria-label": "Put back " + r.name, on: { click: function (ev) {
              busy(ev.currentTarget, function () {
                return call("POST", "channel/restore", { key: k }).then(function (res) { if (res._status !== 200) return failed(null, res); setPassword(pass.value);
                  flash(k, "Put back."); location.reload(); }, function () { rowSay(null, DOWN, true); });
              });
            } } })]));
        });
        tools.appendChild(el("div", {}, [el("h3", { text: "Removed because they didn't play" }),
          el("p", { class: "muted", text: "The nightly check puts each one back after its stream works three nights in a row." }), list]));
      }
      table.closest(".wide").before(tools);
    }
    table.querySelectorAll("tbody tr[data-key]").forEach(function (row) {
      var key = row.dataset.key, cell = el("td", { class: "fav" });
      row.insertBefore(cell, row.querySelector("td.name"));
      if (!can) { if (favorites[key]) cell.appendChild(el("span", { text: "★", title: "Favorite", "aria-label": "Favorite" })); return; }
      var star = el("button", { type: "button", "aria-pressed": favorites[key] ? "true" : "false", text: favorites[key] ? "★" : "☆",
        "aria-label": "Favorite: " + name(row), on: { click: function () {
          if (star.getAttribute("aria-busy")) return;
          var on = star.getAttribute("aria-pressed") !== "true";
          star.setAttribute("aria-busy", "true");  // stays focusable, unlike disabled
          call("PUT", "channel", { key: key, favorite: on }).then(function (r) {
            star.removeAttribute("aria-busy");
            if (r._status !== 200) return failed(row, r);
            setPassword(pass.value);
            star.setAttribute("aria-pressed", on ? "true" : "false");
            star.textContent = on ? "★" : "☆";
            rowSay(row, on ? "Added to favorites, and to the Favorites group in your players." : "Removed from favorites.");
          }, function () { star.removeAttribute("aria-busy"); rowSay(row, DOWN, true); });
        } } });
      cell.appendChild(star);
      // Test sits with the name, so it's in reach on a phone too.
      var test = el("button", { type: "button", class: "test", text: "Test", "aria-label": "Test " + name(row),
        on: { click: function () { testRow(row, key, test); } } });
      row.querySelector("td.name").appendChild(test);
      if (!everything) return;
      var num = row.querySelector("td.num"), current = num.textContent;
      var edit = el("button", { type: "button", class: "num-edit", text: current || "–",
        "aria-label": "Channel number " + (current || "none") + " for " + name(row) + ". Change it", on: { click: function () {
        var box = el("input", { type: "number", min: "1", max: "99999", value: current, "aria-label": "New number for " + name(row) + ". Enter saves, Escape cancels" });
        var finished = false;
        function back() { num.textContent = ""; num.appendChild(edit); edit.focus(); }
        function done(save) {
          if (finished) return;
          finished = true;
          if (!save || box.value.trim() === current) return back();
          var v = box.value.trim();
          call("PUT", "channel", { key: key, number: v ? parseInt(v, 10) : null }).then(function (r) {
            if (r._status !== 200) { back(); return failed(row, r); }
            setPassword(pass.value);
            var moved = (r.moved || []).map(function (m) { return m.name + " moved to " + m.number; });
            flash(key, (v ? name(row) + " is now number " + r.channel.number + "." : name(row) + " no longer holds its number; it keeps " + r.channel.number + " until another channel takes it.") +
              (moved.length ? " " + moved.join(". ") + "." : ""));
            location.reload();
          }, function () { back(); rowSay(row, DOWN, true); });
        }
        box.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); done(true); } if (ev.key === "Escape") done(false); });
        box.addEventListener("blur", function () { done(true); });  // leaving the box saves a change (a phone's Done key does that); Escape cancels
        num.textContent = ""; num.appendChild(box); box.focus(); box.select();
      } } });
      num.textContent = ""; num.appendChild(edit);
    });
    var f = null;
    try { f = JSON.parse(sessionStorage.getItem(FLASH) || "null"); sessionStorage.removeItem(FLASH); } catch (e) {}
    if (f && can) {
      var target = f.key && table.querySelector('tr[data-key="' + CSS.escape(f.key) + '"]');
      rowSay(target || null, f.text);
      (target || msg).scrollIntoView({ block: "center" });
      var focusable = target && target.querySelector("td.num button, td.fav button");
      if (focusable) focusable.focus();
    }
  }).catch(function () {});
})();
</script>
"""
