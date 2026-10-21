"""The Sources page's Channel sources card: the playlists kia reads channels from
(kia.sources, through kia.api). On the secure page, with editing on, each source can be
turned on or off and checked now, and sources can be added; elsewhere the card only lists
them, with a link to the secure page at the top.

Each change answers next to the row or form it came from, and only that row changes, so
the sections someone opened stay open.
"""

STYLE = """
/* Channel sources */
#sources-editor details { border-top: 1px solid var(--line); padding: var(--space-2) 0; }
#sources-editor summary { cursor: pointer; font-weight: 600; min-height: 44px; display: list-item; }
#sources-editor summary .n { color: var(--muted); font-weight: 400; font-size: var(--step--1); margin-left: .5rem; }
.src { display: grid; grid-template-columns: auto 1fr auto; gap: .2rem var(--space-2); align-items: center; padding: .35rem 0; }
.src > label { display: grid; place-items: center; min-width: 44px; min-height: 44px; cursor: pointer; }
.src input[type=checkbox] { width: 1.3rem; height: 1.3rem; }
.src .what { min-width: 0; overflow-wrap: anywhere; }
.src .what small { display: block; color: var(--muted); }
.src .btns { display: flex; gap: var(--space-1); flex-wrap: wrap; justify-content: flex-end; }
.src .btns button { min-height: 40px; padding: .2rem .8rem; font-size: var(--step--1); }
.src-result { grid-column: 2 / 4; margin: 0; }
@media (max-width: 34rem) { .src { grid-template-columns: auto 1fr; } .src .btns { grid-column: 2; justify-content: flex-start; } .src-result { grid-column: 1 / 3; } }
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("sources-editor");
  if (!card) return;
  var body = card.querySelector(".body");
  var KEY = "kia-password";
  var data = null, can = false, pass = null, polling = null, rows = {}, sections = {};

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
    return fetch("api/" + path, { method: method, headers: headers, body: payload ? JSON.stringify(payload) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function n(x) { return (x || 0).toLocaleString(); }
  // A message in a given place: next to the row or inside the form it's about.
  function say(where, text, bad) {
    where.hidden = !text;
    where.className = (where.classList.contains("src-result") ? "src-result " : "") + (bad ? "error" : "preview");
    where.textContent = text || "";
  }
  function failed(where, r) {
    say(where, r.error || "Something went wrong.", true);
    if (r._status === 401) { setPassword(""); pass.focus(); }
  }
  var DOWN = "kia didn't answer. Is it running?";

  function label(p) {
    // "Canada  264" -> "Canada · 264 channels"; other comments as they are.
    var m = /^(.*?)\s+([\d,]+)$/.exec(p.label || "");
    return m ? m[1] + " · " + m[2] + " channels" : (p.label || p.name);
  }
  function streams(s) {
    return s ? n(s.ok) + " working, " + n(s.flaky) + " flaky, " + n(s.dead) + " dead at the last check" : "";
  }

  function count(section) {
    var s = sections[section], on = s.list.filter(function (p) { return p.on; }).length;
    s.count.textContent = on + " of " + s.list.length + " on";
    var used = data.playlists.filter(function (p) { return p.on; }).length;
    card.querySelector(".used").textContent = "kia reads " + n(used) + (used === 1 ? " channel source" : " channel sources") + " now.";
  }

  function row(p) {
    var result = el("p", { class: "src-result", hidden: true, role: "status" });
    var box = el("input", { type: "checkbox", checked: p.on, disabled: !can, "aria-label": "Use " + label(p),
      on: { change: function () {
        box.disabled = true;
        api("PUT", "sources", { url: p.url, on: box.checked }).then(function (r) {
          box.disabled = false;
          if (r._status !== 200) { box.checked = !box.checked; return failed(result, r); }
          setPassword(pass.value);
          p.on = box.checked;
          count(p.section);
          say(result, (p.on ? "On. " : "Off. ") + "This takes effect at the next refresh.");
        }, function () { box.disabled = false; box.checked = !box.checked; say(result, DOWN, true); });
      } } });
    var info = el("div", { class: "what" }, [el("strong", { text: label(p) }), p.radio ? " (radio)" : "",
      el("small", { text: p.name + " · " + p.url }), el("small", { text: streams(p.streams) })]);
    var btns = el("div", { class: "btns" });
    if (can) {
      btns.appendChild(el("button", { type: "button", text: "Check now", "aria-label": "Check " + label(p) + " now",
        on: { click: function () { check(p, result); } } }));
      if (p.origin === "added") btns.appendChild(el("button", { type: "button", class: "danger", text: "Remove",
        "aria-label": "Remove " + label(p), on: { click: function () {
          if (!confirm("Remove " + label(p) + "? Its channels leave your playlists at the next refresh.")) return;
          api("DELETE", "sources?url=" + encodeURIComponent(p.url)).then(function (r) {
            if (r._status !== 200) return failed(result, r);
            setPassword(pass.value);
            data.playlists = data.playlists.filter(function (x) { return x.url !== p.url; });
            sections[p.section].list = sections[p.section].list.filter(function (x) { return x.url !== p.url; });
            rows[p.url].remove(); count(p.section);
          }, function () { say(result, DOWN, true); });
        } } }));
    }
    var c = data.check || {};
    if (c.url === p.url) showCheck(c, result);
    var r = el("div", { class: "src" }, [el("label", {}, [box]), info, btns, result]);
    rows[p.url] = r;
    return r;
  }

  function showCheck(c, where) {
    where.hidden = false;
    where.className = "src-result " + (c.error ? "error" : c.running ? "muted" : "preview");
    if (c.running) {
      var secs = Math.round(Date.now() / 1000 - (c.started || Date.now() / 1000));
      where.textContent = "Checking every stream in it… " + Math.floor(secs / 60) + ":" + String(secs % 60).padStart(2, "0");
      return;
    }
    if (c.error) { where.textContent = c.error; return; }
    var r = c.result || {};
    where.textContent = n(r.total) + " streams: " + n(r.ok) + " working, " + n(r.flaky) + " flaky, " + n(r.dead) + " dead." +
      (r.sample && r.sample.length ? " Working: " + r.sample.join(", ") + (r.ok > r.sample.length ? ", …" : "") : "") +
      " Your playlists don't change until the next refresh.";
  }

  function check(p, where) {
    api("POST", "sources/check", { url: p.url }).then(function (r) {
      if (r._status !== 200) return failed(where, r);
      setPassword(pass.value);
      data.check = r.check; showCheck(r.check, where); poll(where);
    }, function () { say(where, DOWN, true); });
  }

  function poll(where) {
    clearTimeout(polling);
    polling = setTimeout(function () {
      api("GET", "sources/check").then(function (r) {
        data.check = r.check || {};
        showCheck(data.check, where);
        if (data.check.running) poll(where);
      }, function () { say(where, DOWN, true); });
    }, 2000);
  }

  function section(name) {
    if (sections[name]) return sections[name];
    var cnt = el("span", { class: "n" });
    var d = el("details", {}, [el("summary", {}, [name, cnt])]);
    sections[name] = { el: d, count: cnt, list: [] };
    return sections[name];
  }

  function draw() {
    body.textContent = "";
    if (!can && data.editable && card.dataset.secureUrl) {
      body.appendChild(el("p", {}, [el("a", { href: new URL("sources.html#sources-editor", card.dataset.secureUrl).href,
        text: "Turn sources on or off, or add one, on the secure page" }), el("span", { class: "muted", text: " (https://)." })]));
    }
    body.appendChild(el("p", {}, [el("span", { class: "used" }), " " +
      (can ? "Tick a source to use its channels, untick it to stop. " : "") +
      "Changes take effect at the next refresh. Choices made here are kept apart from " + data.file +
      ", which you can still edit by hand."]));
    if (can) {
      pass = el("input", { type: "password", id: "src-pass", autocomplete: "current-password", value: getPassword() });
      body.appendChild(el("div", { class: "editor" }, [el("label", { class: "field", for: "src-pass" }, [el("strong", { text: "Password" }),
        el("span", { class: "hint", text: "Needed to make changes. This tab remembers it until you close it." })]), pass]));
    }
    data.playlists.forEach(function (p) { section(p.section).list.push(p); });
    Object.keys(sections).forEach(function (name) {
      var s = sections[name];
      s.el.open = s.list.some(function (p) { return p.on; });
      s.list.forEach(function (p) { s.el.appendChild(row(p)); });
      body.appendChild(s.el);
      count(name);
    });
    if (can) body.appendChild(addForm());
  }

  function addForm() {
    var url = el("input", { type: "url", id: "src-url", inputmode: "url", placeholder: "https://example.com/playlist.m3u", spellcheck: "false" });
    var name = el("input", { type: "text", id: "src-name", placeholder: "e.g. extras", maxlength: "40", spellcheck: "false" });
    var radio = el("input", { type: "checkbox", id: "src-radio" });
    var button = el("button", { type: "submit", class: "primary", text: "Add the source" });
    var result = el("p", { role: "status", hidden: true });
    var form = el("form", { class: "editor" }, [el("h3", { text: "Add a channel source" }),
      el("label", { class: "field", for: "src-url" }, [el("strong", { text: "Address" }),
        el("span", { class: "hint", text: "An M3U playlist on the internet (http:// or https://)." })]), url,
      el("label", { class: "field", for: "src-name" }, [el("strong", { text: "Short name (optional)" }),
        el("span", { class: "hint", text: "Used in the reports and in your playlists' settings. Spaces become dashes." })]), name,
      el("label", { class: "choice", for: "src-radio" }, [radio, "Everything in it is a radio station"]),
      result, el("div", { class: "actions" }, [button])]);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var short = name.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
      button.disabled = true; button.textContent = "Adding…";
      api("POST", "sources", { url: url.value, name: short, radio: radio.checked }).then(function (r) {
        button.disabled = false; button.textContent = "Add the source";
        if (r._status !== 200) return failed(result, r);
        setPassword(pass.value);
        data = r; sections = {}; rows = {}; draw();
        var added = data.playlists.filter(function (p) { return p.url === url.value.trim(); })[0];
        if (added) { var s = sections[added.section]; s.el.open = true; rows[added.url].scrollIntoView({ block: "center" });
          say(rows[added.url].querySelector(".src-result"), "Added. Its channels join your playlists at the next refresh."); }
      }, function () { button.disabled = false; button.textContent = "Add the source"; say(result, DOWN, true); });
    });
    return form;
  }

  fetch("api/sources").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d || !d.playlists) {
      if (location.protocol === "file:") { card.hidden = false; body.textContent = "Open this page through kia's address to see and change the channel sources."; }
      return;
    }
    data = d;
    can = d.editable && d.secure;
    card.hidden = false;
    draw();
    if (d.check && d.check.running && rows[d.check.url]) poll(rows[d.check.url].querySelector(".src-result"));
  }).catch(function () {});
})();
</script>
"""
