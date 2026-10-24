"""The Stack page: what runs next to kia, and on the secure page, choosing what runs.

kia can't reach Docker, so the page asks the kia-stack service on the computer to check or
apply a choice (kia.stack, docs/stack.md). The answer comes a few seconds later, so the page
asks kia for it every two seconds. An apply that changes what runs restarts kia too, so the
page keeps asking, less often, until kia is back and the answer is there.

Apply is offered only after a Check of the same choice found no problems, and asks first if
someone is watching, or if parts that run now would stop: the service applies without
asking, so the page asks instead.
"""

STYLE = """
/* The Stack page (with the shared form styles, editor.STYLE) */
#stack-page section + section { margin-top: var(--space-4); }
#stack-page h2 { margin-top: 0; }
#stack-page table { width: 100%; border-collapse: collapse; }
#stack-page th, #stack-page td { text-align: left; padding: .4rem .5rem; border-top: 1px solid var(--line); vertical-align: top; }
#stack-page th { color: var(--muted); font-weight: 600; font-size: var(--step--1); }
#stack-page .bad { color: var(--bad); font-weight: 600; }
#stack-page .good { color: var(--good); }
.parts { display: grid; gap: .1rem; margin: var(--space-2) 0; }
.parts label { display: grid; grid-template-columns: auto 1fr; gap: var(--space-2); align-items: start; min-height: 44px; padding: .3rem 0; cursor: pointer; }
.parts input[type=checkbox] { width: 1.3rem; height: 1.3rem; margin-top: .15rem; }
.parts small { display: block; color: var(--muted); }
.presets { display: flex; flex-wrap: wrap; gap: var(--space-1); margin: var(--space-1) 0 var(--space-2); }
.presets button { min-height: 40px; padding: .2rem .8rem; font-size: var(--step--1); }
#stack-page pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg); border: 1px solid var(--line);
  border-radius: var(--radius-small); padding: var(--space-2); font-size: var(--step--1); max-height: 24rem; overflow: auto; }
#stack-page code.command { display: block; overflow-wrap: anywhere; padding: .5rem .7rem; background: var(--bg);
  border: 1px solid var(--line); border-radius: var(--radius-small); }
@media (pointer: coarse) { .presets button { min-height: 44px; } }
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("stack-page");
  if (!card) return;
  var body = card.querySelector(".body");
  var KEY = "kia-password";  // the same key as the other pages, so one password does for all
  var data = null, pass = null, boxes = {}, checked = null, polling = null;

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
  function api(method, path, payload) {
    var headers = { "Content-Type": "application/json" };
    if (pass && pass.value) headers["X-Kia-Password"] = pass.value;
    return fetch("api/" + path, { method: method, headers: headers, body: payload ? JSON.stringify(payload) : undefined, cache: "no-cache" })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  var DOWN = "kia didn't answer. Is it running?";

  // The parts ticked now, in the order kia lists them.
  function picked() { return data.parts.map(function (p) { return p.name; }).filter(function (n) { return boxes[n] && boxes[n].checked; }); }
  function same(a, b) { return a.join(",") === b.join(","); }

  // --- What runs now ---------------------------------------------------------------
  function running() {
    var part = el("section", {}, [el("h2", { text: "What runs now" })]);
    var chosen = data.chosen.length ? data.chosen.join(", ") : "kia and its web server only";
    part.appendChild(el("p", {}, ["Chosen: ", el("strong", { text: chosen }), el("span", { class: "muted",
      text: data.chosen.length ? " (KIA_STACK in .env)" : "" })]));
    if (!data.service) {
      part.appendChild(el("p", { class: "muted", text: "Each program's own state shows here once the kia-stack service runs on this computer (below). " +
        "Until then, the Dashboard's Your stack tiles say which programs answer." }));
      return part;
    }
    var rows = data.containers.map(function (c) {
      var bad = c.state !== "running" || c.health === "unhealthy";
      if (c.state === "exited" && /Exited \(0\)/.test(c.status)) bad = false;  // a setup step that finished
      return el("tr", {}, [el("td", { text: c.service }), el("td", { class: bad ? "bad" : "good", text: c.state + (c.health ? ", " + c.health : "") }),
        el("td", { class: "muted", text: c.status })]);
    });
    part.appendChild(el("table", {}, [el("thead", {}, [el("tr", {}, [el("th", { text: "Program" }), el("th", { text: "State" }), el("th", { text: "Docker says" })])]),
      el("tbody", {}, rows)]));
    return part;
  }

  // --- Choosing what runs ----------------------------------------------------------
  function chooser() {
    var part = el("section", {}, [el("h2", { text: "Choose what runs" })]);
    if (!data.secure) {
      if (data.editable && card.dataset.secureUrl) {
        part.appendChild(el("p", {}, [el("a", { href: new URL("stack.html", card.dataset.secureUrl).href, text: "Choose on the secure page" }),
          el("span", { class: "muted", text: " (https://), with kia's password." })]));
      }
      part.appendChild(el("p", { class: "muted" }, ["Or in the kia folder: ", el("code", { text: "scripts/stack.sh apply" }), " (", el("a", { href: "https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/stack.md", text: "how" }), ")."]));
      return part;
    }
    if (!data.editable) {
      part.appendChild(el("p", { class: "muted", text: "Editing is off (KIA_EDITING=false), so choose in the kia folder with scripts/stack.sh." }));
      return part;
    }
    pass = el("input", { type: "password", id: "stack-pass", autocomplete: "current-password", value: getPassword() });
    part.appendChild(el("div", { class: "editor" }, [el("label", { class: "field", for: "stack-pass" }, [el("strong", { text: "Password" }),
      el("span", { class: "hint", text: "Needed to check or apply. This tab remembers it until you close it." })]), pass]));

    var presets = el("div", { class: "presets", role: "group", "aria-label": "Presets" });
    data.presets.forEach(function (p) {
      presets.appendChild(el("button", { type: "button", text: p.name, title: p.what, "aria-label": "Preset " + p.name + ": " + p.what,
        on: { click: function () { data.parts.forEach(function (x) { boxes[x.name].checked = p.parts.indexOf(x.name) >= 0; }); changed(); } } }));
    });
    part.appendChild(el("p", { class: "muted", text: "Start from a preset, or tick the parts yourself." }));
    part.appendChild(presets);

    var list = el("div", { class: "parts" });
    boxes = {};
    data.parts.forEach(function (p) {
      var box = el("input", { type: "checkbox", id: "part-" + p.name, checked: data.chosen.indexOf(p.name) >= 0, on: { change: changed } });
      boxes[p.name] = box;
      list.appendChild(el("label", { for: "part-" + p.name }, [box, el("span", {}, [el("strong", { text: p.name }), el("small", { text: p.what })])]));
    });
    part.appendChild(list);

    var command = el("code", { class: "command" });
    part.appendChild(el("p", { class: "muted", text: data.service ? "The same from the kia folder:" :
      "The kia-stack service isn't running on this computer, so this page can't make the change. Run this in the kia folder instead:" }));
    part.appendChild(command);
    if (!data.service) {
      part.appendChild(el("p", { class: "muted" }, ["To make changes here, install the service once: ", el("code", { text: "scripts/install-service.sh --stack" }),
        " (", el("a", { href: "https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/stack.md#changing-the-stack-from-the-dashboard", text: "how" }), ")."]));
    }
    var check = el("button", { type: "button", class: "primary", text: "Check", disabled: !data.service, on: { click: function () { ask("check"); } } });
    var apply = el("button", { type: "button", text: "Apply", disabled: true, on: { click: applyIt } });
    part.appendChild(el("div", { class: "actions" }, [check, apply]));
    var status = el("p", { role: "status", class: "stack-status" });
    var output = el("pre", { hidden: true });
    part.appendChild(status);
    part.appendChild(output);

    function changed() {
      var parts = picked();
      command.textContent = "scripts/stack.sh apply --stack " + (parts.join(",") || '""');
      // Apply only what Check just looked at and found fine.
      apply.disabled = !data.service || !checked || !same(checked.parts, parts) || checked.code !== "0";
    }
    card.changed = changed;
    card.say = function (text, bad) { status.className = "stack-status " + (bad ? "error" : "preview"); status.textContent = text || ""; };
    card.show = function (text) { output.hidden = !text; output.textContent = text || ""; };
    card.buttons = [check, apply];
    changed();
    return part;
  }

  // --- Asking the service, and waiting for its answer ------------------------------
  function ask(action, sure) {
    var parts = picked();
    card.buttons.forEach(function (b) { b.disabled = true; });
    card.say(action === "check" ? "Asking the kia-stack service to check…" : "Asking the kia-stack service to apply…");
    card.show("");
    api("POST", "stack/" + action, { parts: parts }).then(function (r) {
      if (r._status !== 200) {
        card.say(r.error || "Something went wrong.", true);
        if (r._status === 401) { setPassword(""); pass.focus(); }
        card.buttons[0].disabled = !data.service; card.changed();
        return;
      }
      setPassword(pass.value);
      wait(r.id, action, parts, Date.now(), 2000);
    }, function () { card.say(DOWN, true); card.buttons[0].disabled = !data.service; card.changed(); });
  }

  // Every two seconds until the answer to this request is there. kia may be restarting
  // (an apply that changes what runs restarts it), so a missing answer means "try again,
  // less often", for up to five minutes.
  function wait(id, action, parts, since, every) {
    clearTimeout(polling);
    polling = setTimeout(function () {
      var lost = function () {
        if (Date.now() - since > 300000) { card.say("No answer after five minutes. Check the kia-stack service's log on the computer.", true); return finish(); }
        card.say(action === "apply" ? "kia is restarting with the new choice… (this page keeps asking)" : "kia didn't answer for a moment, trying again…");
        wait(id, action, parts, since, Math.min(every * 2, 15000));
      };
      fetch("api/stack", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d) return lost();
        var res = d.result;
        if (res && res.id === id && (res.state === "finished" || res.state === "refused")) {
          data = Object.assign(data, d);
          return answered(res, action, parts);
        }
        if (Date.now() - since > 30000 && d.waiting === id && !d.service) {
          card.say("The kia-stack service hasn't picked the request up. Is it running on the computer?", true);
        } else {
          card.say(res && res.id === id && res.state === "running" ? (action === "check" ? "Checking…" : "Applying… this can take a minute or two.") : "Waiting for the kia-stack service…");
        }
        wait(id, action, parts, since, 2000);
      }, lost);
    }, every);
  }

  function answered(res, action, parts) {
    card.show(res.output);
    var ok = res.exit === "0";
    if (res.state === "refused") card.say("The kia-stack service refused it: see below.", true);
    else if (action === "check") card.say(ok ? "No problems. Apply makes the change." : "Check found problems: see below. Apply stays off until they're fixed.", !ok);
    else card.say(ok ? "Done. What runs now is below." : "The change didn't finish: see below.", !ok);
    checked = action === "check" ? { parts: parts, code: res.exit, output: res.output } : null;
    if (action === "apply") { draw(); card.show(res.output); card.say(ok ? "Done. What runs now is above." : "The change didn't finish: see below.", !ok); return; }
    finish();
  }
  function finish() { card.buttons[0].disabled = !data.service; card.changed(); }

  // Before an apply: anyone watching (as scripts/stack.sh would ask), and parts that stop.
  function applyIt() {
    var stops = /Stops what runs now: ([^\n(]*)/.exec(checked && checked.output || "");
    fetch("metrics", { cache: "no-cache" }).then(function (r) { return r.ok ? r.text() : ""; }, function () { return ""; }).then(function (text) {
      var n = 0;
      text.split("\n").forEach(function (line) {
        var m = /^kia_pluto_channels_playing (\d+)/.exec(line);
        if (m) n += +m[1];
        if (/^kia_(plex|jellyfin)_sessions\{/.test(line) && line.indexOf('kind="none"') < 0) n += +line.split(" ").pop() || 0;
      });
      var warn = [];
      if (n) warn.push(n + (n === 1 ? " player is" : " players are") + " watching through kia now, and may be cut off.");
      if (stops) warn.push("These stop: " + stops[1].trim() + ".");
      if (warn.length && !confirm(warn.join(" ") + " Apply anyway?")) return;
      ask("apply");
    });
  }

  function draw() {
    body.textContent = "";
    if (!data.docker) {
      body.appendChild(el("p", { text: "kia runs without Docker here, so there are no other programs next to it to choose. " +
        "Plex, Jellyfin, and the graphs come with kia's Docker setup." }));
      return;
    }
    body.appendChild(running());
    body.appendChild(chooser());
  }

  // If a request is under way when the page opens (another tab, or a reload), follow it.
  function resume() {
    if (data.waiting && data.secure && card.say) {
      card.buttons.forEach(function (b) { b.disabled = true; });
      wait(data.waiting, data.result && data.result.id === data.waiting ? data.result.action : "check", picked(), Date.now(), 0);
    }
  }

  fetch("api/stack", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d) {
      if (location.protocol === "file:") { card.hidden = false; body.textContent = "Open this page through kia's address to see the stack."; }
      return;
    }
    data = d; card.hidden = false; draw(); resume();
  }, function () {});
})();
</script>
"""
