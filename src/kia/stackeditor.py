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
.preset-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: var(--space-2); margin: var(--space-2) 0; }
.preset-card { display: grid; gap: .3rem; align-content: start; text-align: left; padding: var(--space-2) var(--space-3); border-radius: var(--radius-small);
  background: var(--card); color: var(--fg); border: 1px solid var(--control); font: inherit; cursor: pointer; min-height: 44px; }
.preset-card .what { color: var(--fg); font-size: var(--step--1); }
.preset-card small { color: var(--muted); }
.preset-card[aria-pressed="true"] { border: 2px solid var(--info); background: var(--info-bg); }
.own-parts { margin: var(--space-2) 0; }
.own-parts summary { cursor: pointer; font-weight: 600; min-height: 44px; display: list-item; }
.next-steps { border-top: 1px solid var(--line); margin-top: var(--space-3); padding-top: var(--space-3); }
.next-steps li { margin-bottom: var(--space-2); }
.next-steps .warn { background: var(--warn-bg); border-radius: var(--radius-small); padding: .5rem .7rem; }
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
      // A remembered password goes in as the field's value, never an attribute in the page.
      else if (k === "value") e.value = attrs[k];
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

    // The presets (stacks/*.env) as cards: choosing one ticks its parts. Or the parts one by
    // one, below, for a combination no card has.
    part.appendChild(el("p", { class: "muted", text: "Choose one of these, or choose the parts yourself below." }));
    var cards = el("div", { class: "preset-cards", role: "group", "aria-label": "Presets" });
    var cardButtons = [];
    data.presets.forEach(function (p) {
      var b = el("button", { type: "button", class: "preset-card", "aria-pressed": "false",
        on: { click: function () { data.parts.forEach(function (x) { boxes[x.name].checked = p.parts.indexOf(x.name) >= 0; }); changed(); } } },
        [el("strong", { text: p.title }), el("span", { class: "what", text: p.what.replace(/\s*\(docs\/[^)]*\)\.?$/, ".") }),
         el("small", { text: p.needs }),
         // What it asks of you, before you choose it: a claim code, a folder, an API key.
         p.asks && p.asks !== "Nothing more." ? el("small", { class: "asks", text: "Asks you for: " + p.asks }) : null]);
      b.parts = p.parts.slice().sort();
      cardButtons.push(b);
      cards.appendChild(b);
    });
    part.appendChild(cards);

    var list = el("div", { class: "parts" });
    boxes = {};
    data.parts.forEach(function (p) {
      var box = el("input", { type: "checkbox", id: "part-" + p.name, checked: data.chosen.indexOf(p.name) >= 0, on: { change: changed } });
      boxes[p.name] = box;
      list.appendChild(el("label", { for: "part-" + p.name }, [box, el("span", {}, [el("strong", { text: p.name }), el("small", { text: p.what })])]));
    });
    var own = el("details", { class: "own-parts" }, [el("summary", { text: "Choose the parts yourself" }), list]);
    part.appendChild(own);

    // Plex's first start links it to your Plex account with a claim code, which only you can
    // get, and which works for 4 minutes: asked for here, right before Apply.
    var plexRuns = data.containers.some(function (c) { return c.service === "plex"; });
    var claim = el("input", { type: "text", id: "stack-claim", autocomplete: "off", spellcheck: "false", placeholder: "claim-…" });
    var claimBox = el("div", { class: "editor", hidden: true }, [el("label", { class: "field", for: "stack-claim" }, [
      el("strong", { text: "Plex claim code (for Plex's first start)" }),
      el("span", { class: "hint" }, ["It links the new Plex to your Plex account. Sign in at ",
        el("a", { href: "https://plex.tv/claim", target: "_blank", rel: "noopener", text: "plex.tv/claim" }),
        ", copy the code here, and choose Apply within 4 minutes. Check first: the code doesn't last long."])]), claim]);
    part.appendChild(claimBox);
    card.claim = function () { return claimBox.hidden ? "" : claim.value.trim(); };

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
      var key = parts.slice().sort().join(","), matched = false;
      cardButtons.forEach(function (b) {
        var on = b.parts.join(",") === key && !matched;
        if (on) matched = true;
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (!matched) own.open = true;  // a combination of its own: show the parts
      claimBox.hidden = !(boxes.plex && boxes.plex.checked) || plexRuns;
      // Apply only what Check just looked at and found fine.
      apply.disabled = !data.service || !checked || !same(checked.parts, parts) || checked.code !== "0";
    }
    card.changed = changed;
    card.say = function (text, bad) { status.className = "stack-status " + (bad ? "error" : "preview"); status.textContent = text || ""; };
    card.show = function (text) { output.hidden = !text; output.textContent = text || ""; };
    card.buttons = [check, apply];
    card.section = part;  // not card.part: every element has a built-in part
    changed();
    return part;
  }

  // --- Asking the service, and waiting for its answer ------------------------------
  function ask(action, sure) {
    var parts = picked();
    card.buttons.forEach(function (b) { b.disabled = true; });
    card.say(action === "check" ? "Asking the kia-stack service to check…" : "Asking the kia-stack service to apply…");
    card.show("");
    var body = { parts: parts };
    if (action === "apply" && card.claim()) body.claim = card.claim();
    card.before = data.chosen.slice();  // to tell, after an apply, which parts are new
    api("POST", "stack/" + action, body).then(function (r) {
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

  // Every two seconds until the answer to this request is there. While the service
  // works on it, keep asking, however long an apply takes (it can download images). kia
  // may restart too (an apply that changes what runs restarts it), so no answer at all
  // means "try again, less often", for up to five minutes.
  function wait(id, action, parts, since, every, lostSince) {
    clearTimeout(polling);
    polling = setTimeout(function () {
      var lost = function () {
        var gone = lostSince || Date.now();
        if (Date.now() - gone > 300000) { card.say("No answer from kia for five minutes. Is kia running?", true); return finish(); }
        card.say(action === "apply" ? "kia is restarting with the new choice… (this page keeps asking)" : "kia didn't answer for a moment, trying again…");
        wait(id, action, parts, since, Math.min(every * 2, 15000), gone);
      };
      fetch("api/stack", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d) return lost();
        var res = d.result;
        if (res && res.id === id && (res.state === "finished" || res.state === "refused")) {
          data = Object.assign(data, d);
          return answered(res, action, parts);
        }
        if (d.waiting !== id && !(res && res.id === id)) {
          // Neither waiting nor answered: the service never took it, and it has expired.
          card.say("The kia-stack service didn't pick the request up within 5 minutes. Is it running on the computer?", true);
          return finish();
        }
        if (Date.now() - since > 30000 && !d.service) {
          card.say("The kia-stack service hasn't picked the request up. Is it running on the computer?", true);
        } else {
          card.say(res && res.id === id && res.state === "running" ? (action === "check" ? "Checking…" : "Applying… this can take a few minutes the first time, while Docker downloads.") : "Waiting for the kia-stack service…");
        }
        wait(id, action, parts, since, 2000);
      }, lost);
    }, every);
  }

  function answered(res, action, parts) {
    var ok = res.exit === "0";
    // After an apply, what runs has changed: draw the page again, then show the answer.
    if (action === "apply") draw();
    card.show(res.output);
    if (res.state === "refused") card.say("The kia-stack service refused it: see below.", true);
    else if (action === "check") card.say(ok ? "No problems. Apply makes the change." : "Check found problems: see below. Apply stays off until they're fixed.", !ok);
    else card.say(ok ? "Done. What runs now is above." : "The change didn't finish: see below.", !ok);
    // Apply needs a new Check after this: of this choice, if this was one that passed.
    checked = action === "check" ? { parts: parts, code: res.exit, output: res.output } : null;
    if (action === "check" && /no playlist named plex/.test(res.output || "")) playlistHelp();
    if (action === "apply" && ok) {
      var added = data.chosen.filter(function (n) { return (card.before || []).indexOf(n) < 0; });
      if (added.length) card.section.appendChild(nextSteps(added));
    }
    finish();
  }

  // Threadfin needs kia's "plex" playlist before Plex can have channels. When the check says
  // it's missing, offer to make it: all TV channels, if they fit in Plex's 480, or else the
  // Playlists page, to pick categories.
  function playlistHelp() {
    var box = el("div", { class: "editor" });
    card.section.appendChild(box);
    api("POST", "preview", { radio: false }).then(function (r) {
      var n = r.tv || 0;
      if (r._status !== 200 || !n) { box.appendChild(el("p", { text: "Make it on the Playlists page: choose Make a playlist, name it plex, and tick For Plex through Threadfin." })); return; }
      if (n > 480) {
        box.appendChild(el("p", {}, ["kia has " + n.toLocaleString() + " TV channels, and Plex takes at most 480 from one tuner. On ",
          el("a", { href: "playlists.html", text: "Playlists" }), ", choose Make a playlist, name it plex, tick the categories you watch until it's 480 or fewer, and tick For Plex through Threadfin. Then Check again."]));
        return;
      }
      var make = el("button", { type: "button", class: "primary", text: "Make the plex playlist: all " + n.toLocaleString() + " TV channels" });
      var said = el("p", { role: "status" });
      make.addEventListener("click", function () {
        make.disabled = true;
        api("PUT", "profiles/plex", { title: "Plex", radio: false, threadfin: true }).then(function (r2) {
          if (r2._status !== 200) { make.disabled = false; said.className = "error"; said.textContent = r2.error || "Something went wrong."; return; }
          said.className = "preview";
          said.textContent = "Made. Choose Check again.";
        }, function () { make.disabled = false; said.className = "error"; said.textContent = DOWN; });
      });
      box.appendChild(el("p", { text: "Threadfin, Plex's tuner, reads kia's playlist named plex, which doesn't exist yet." }));
      box.appendChild(el("div", { class: "actions" }, [make]));
      box.appendChild(said);
    }, function () { box.appendChild(el("p", { class: "error", text: DOWN })); });
  }

  // After an apply: what each new part needs from you, once, to be ready.
  function nextSteps(added) {
    // Browsers give an IPv6 address with its [ ] already (location.hostname is "[fd00::5]").
    var host = location.hostname.indexOf(":") >= 0 && location.hostname.charAt(0) !== "[" ? "[" + location.hostname + "]" : location.hostname;
    var links = data.links || {};
    var items = el("ol");
    var docs = "https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/";
    function step(kids) { items.appendChild(el("li", {}, kids)); }
    if (added.indexOf("jellyfin") >= 0) {
      var jf = "http://" + host + ":" + (links.jellyfin || 8096);
      step([el("p", { class: "warn" }, [el("strong", { text: "Jellyfin: finish its setup now. " }),
        "Until you do, anyone on your network who opens its page can make themselves its administrator. Open ",
        el("a", { href: jf, target: "_blank", rel: "noopener", text: jf }), ", then add kia's channels as live TV (",
        el("a", { href: docs + "cookbooks/jellyfin-box.md#3-set-up-jellyfin", target: "_blank", rel: "noopener", text: "how" }), ")."])]);
    }
    if (added.indexOf("plex") >= 0 || added.indexOf("plex-tuner") >= 0) {
      var out = el("pre", { hidden: true }), said = el("p", { role: "status" });
      var go = el("button", { type: "button", class: "primary", text: "Add Live TV to Plex now" });
      go.addEventListener("click", function () { go.disabled = true; addLiveTv(go, said, out); });
      var parts = [el("p", { text: "Plex: kia adds its channels to Plex as Live TV by itself within 4 hours, or now with this button. The first time can take a few minutes." }),
        el("div", { class: "actions" }, [go]), said, out];
      if (added.indexOf("plex") >= 0) {
        var px = "http://" + host + ":" + (links.plex || 32400) + "/web";
        parts.push(el("p", {}, ["Then open ", el("a", { href: px, target: "_blank", rel: "noopener", text: "Plex" }), ": Live TV has your channels."]));
      } else {
        parts.push(el("p", { class: "muted" }, ["kia needs KIA_PLEX_URL and KIA_PLEX_TOKEN in .env to reach your Plex; or add the tuner in Plex's settings (",
          el("a", { href: docs + "plex.md#4-add-threadfin-to-plex", target: "_blank", rel: "noopener", text: "how" }), ")."]));
      }
      step(parts);
    }
    if (added.indexOf("observability") >= 0) {
      var g = links.grafana;
      step([el("p", {}, ["Graphs: open ", g ? el("a", { href: g, target: "_blank", rel: "noopener", text: "Grafana" }) : "Grafana (port 8788, https)",
        " and sign in as admin. The password is the one you set in KIA_GRAFANA_PASSWORD, or in monitoring/grafana/admin-password.txt in the kia folder. The Dashboard's Observability button opens it too."])]);
    }
    return el("section", { class: "next-steps" }, [el("h3", { text: "What's next" }), items]);
  }

  // kia plex-tuners, in kia, followed until it's done.
  function addLiveTv(button, said, out) {
    said.className = "muted"; said.textContent = "Adding kia's channels to Plex…";
    api("POST", "plex/tuners").then(function (r) {
      if (r._status !== 200) { button.disabled = false; said.className = "error"; said.textContent = r.error || "Something went wrong."; return; }
      (function poll() {
        setTimeout(function () {
          fetch("api/plex/tuners", { cache: "no-cache" }).then(function (x) { return x.ok ? x.json() : null; }).then(function (d) {
            var t = d && d.plex;
            if (!t || t.running) return poll();
            out.hidden = !t.output; out.textContent = t.output || "";
            said.className = t.ok ? "preview" : "error";
            said.textContent = t.ok ? "Done. Plex loads the guide now; the channels show under Live TV in a few minutes." : "That didn't finish: see below.";
            button.disabled = !!t.ok;
          }, poll);
        }, 3000);
      })();
    }, function () { button.disabled = false; said.className = "error"; said.textContent = DOWN; });
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
