"""The Details page's Playlists card: choose the playlists kia checks (kia.sources, through
kia.api). On the secure page, with editing on, each playlist can be turned on or off and
checked now, and playlists can be added; elsewhere the card only lists them.
"""

STYLE = """
/* Choosing playlists */
#sources-editor details { border-top: 1px solid var(--line); padding: var(--space-2) 0; }
#sources-editor summary { cursor: pointer; font-weight: 600; min-height: 36px; display: flex; align-items: center; gap: .5rem; }
#sources-editor summary .n { color: var(--muted); font-weight: 400; font-size: var(--step--1); }
.src { display: grid; grid-template-columns: auto 1fr auto; gap: .2rem var(--space-2); align-items: center; padding: .35rem 0; }
.src input[type=checkbox] { width: 1.2rem; height: 1.2rem; }
.src .what { min-width: 0; overflow-wrap: anywhere; }
.src .what small { display: block; color: var(--muted); }
.src .btns { display: flex; gap: var(--space-1); flex-wrap: wrap; justify-content: flex-end; }
.src .btns button { min-height: 36px; padding: .2rem .7rem; font-size: var(--step--1); }
.src-result { grid-column: 2 / 4; }
@media (max-width: 34rem) { .src { grid-template-columns: auto 1fr; } .src .btns { grid-column: 2; justify-content: flex-start; } .src-result { grid-column: 1 / 3; } }
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("sources-editor");
  if (!card) return;
  var body = card.querySelector(".body");
  var KEY = "kia-password";
  var data = null, can = false, pass = null, msg = null, polling = null;

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
  function say(text, bad) { msg.hidden = !text; msg.className = bad ? "error" : "preview"; msg.textContent = text || ""; }
  function failed(r) {
    say(r.error || "Something went wrong.", true);
    if (r._status === 401) { setPassword(""); pass.focus(); }
  }
  function saved(r) {
    if (r._status !== 200) return failed(r);
    setPassword(pass.value);
    data = r; draw();
    say("Saved. The change takes effect at the next refresh.", false);
  }

  function streams(s) {
    if (!s) return "";
    return n(s.ok) + " working, " + n(s.flaky) + " flaky, " + n(s.dead) + " dead at the last check";
  }

  function row(p) {
    var box = el("input", { type: "checkbox", checked: p.on, disabled: !can, "aria-label": "Use " + p.name,
      on: { change: function () {
        box.disabled = true;
        api("PUT", "sources", { url: p.url, on: box.checked }).then(function (r) { box.disabled = false; saved(r); if (r._status !== 200) box.checked = !box.checked; },
          function () { box.disabled = false; box.checked = !box.checked; say("kia didn't answer. Is it running?", true); });
      } } });
    var info = el("div", { class: "what" }, [el("strong", { text: p.name }), p.radio ? " (radio)" : "",
      p.label ? " " + p.label : "", el("small", { text: p.url }), el("small", { text: streams(p.streams) })]);
    var btns = el("div", { class: "btns" });
    var result = el("p", { class: "src-result muted", hidden: true });
    if (can) {
      btns.appendChild(el("button", { type: "button", text: "Check now", on: { click: function () { check(p, result); } } }));
      if (p.origin === "added") btns.appendChild(el("button", { type: "button", class: "danger", text: "Remove", on: { click: function () {
        if (!confirm("Remove " + p.name + "? Its channels leave your playlists at the next refresh.")) return;
        api("DELETE", "sources?url=" + encodeURIComponent(p.url)).then(saved, function () { say("kia didn't answer. Is it running?", true); });
      } } }));
    }
    var c = data.check || {};
    if (c.url === p.url) showCheck(c, result);
    return el("div", { class: "src" }, [box, info, btns, result]);
  }

  function showCheck(c, where) {
    where.hidden = false;
    where.className = "src-result " + (c.error ? "error" : c.running ? "muted" : "preview");
    if (c.running) { where.textContent = "Checking every stream in it. This can take a few minutes…"; return; }
    if (c.error) { where.textContent = c.error; return; }
    var r = c.result || {};
    where.textContent = n(r.total) + " streams: " + n(r.ok) + " working, " + n(r.flaky) + " flaky, " + n(r.dead) + " dead." +
      (r.sample && r.sample.length ? " Working: " + r.sample.join(", ") + (r.ok > r.sample.length ? ", …" : "") : "") +
      " Your playlists don't change until the next refresh.";
  }

  function check(p, where) {
    say("");
    api("POST", "sources/check", { url: p.url }).then(function (r) {
      if (r._status !== 200) return failed(r);
      setPassword(pass.value);
      data.check = r.check; showCheck(r.check, where); poll(where);
    }, function () { say("kia didn't answer. Is it running?", true); });
  }

  function poll(where) {
    clearTimeout(polling);
    polling = setTimeout(function () {
      api("GET", "sources/check").then(function (r) {
        data.check = r.check || {};
        showCheck(data.check, where);
        if (data.check.running) poll(where);
      });
    }, 3000);
  }

  function draw() {
    body.textContent = "";
    var sections = [], by = {};
    data.playlists.forEach(function (p) {
      if (!by[p.section]) { by[p.section] = []; sections.push(p.section); }
      by[p.section].push(p);
    });
    var used = data.playlists.filter(function (p) { return p.on; }).length;
    body.appendChild(el("p", {}, ["kia checks " + n(used) + (used === 1 ? " playlist" : " playlists") + " now. " +
      (can ? "Tick a playlist to use it, untick it to stop. " : "") +
      "Changes take effect at the next refresh. Choices made here are kept apart from " + data.file +
      ", which you can still edit by hand."]));
    if (can) {
      pass = pass || el("input", { type: "password", id: "src-pass", autocomplete: "current-password", value: getPassword() });
      body.appendChild(el("div", { class: "editor" }, [el("label", { class: "field", for: "src-pass" }, [el("strong", { text: "Password" }),
        el("span", { class: "hint", text: "Needed to make changes. This tab remembers it until you close it." })]), pass]));
    }
    body.appendChild(msg);
    sections.forEach(function (name) {
      var list = by[name], on = list.filter(function (p) { return p.on; }).length;
      var d = el("details", { open: on > 0 }, [el("summary", {}, [name, el("span", { class: "n", text: on + " of " + list.length + " on" })])]);
      list.forEach(function (p) { d.appendChild(row(p)); });
      body.appendChild(d);
    });
    if (can) body.appendChild(addForm());
  }

  function addForm() {
    var url = el("input", { type: "text", id: "src-url", inputmode: "url", placeholder: "https://example.com/playlist.m3u", spellcheck: "false" });
    var name = el("input", { type: "text", id: "src-name", placeholder: "e.g. extras", maxlength: "40", spellcheck: "false" });
    var radio = el("input", { type: "checkbox", id: "src-radio" });
    var button = el("button", { type: "submit", class: "primary", text: "Add the playlist" });
    var form = el("form", { class: "editor" }, [el("h3", { text: "Add a playlist" }),
      el("label", { class: "field", for: "src-url" }, [el("strong", { text: "Address" }),
        el("span", { class: "hint", text: "An M3U playlist on the internet (http:// or https://)." })]), url,
      el("label", { class: "field", for: "src-name" }, [el("strong", { text: "Short name (optional)" }),
        el("span", { class: "hint", text: "Used in the reports and in profiles. Lowercase letters, numbers, and dashes." })]), name,
      el("label", { class: "choice", for: "src-radio" }, [radio, "Everything in it is a radio station"]),
      el("div", { class: "actions" }, [button])]);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      button.disabled = true;
      api("POST", "sources", { url: url.value, name: name.value, radio: radio.checked }).then(function (r) { button.disabled = false; saved(r); },
        function () { button.disabled = false; say("kia didn't answer. Is it running?", true); });
    });
    return form;
  }

  fetch("api/sources").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d || !d.playlists) return;  // no API here, or playlists come from the command line
    data = d;
    can = d.editable && d.secure;
    msg = el("p", { role: "status", hidden: true });
    card.hidden = false;
    draw();
    if (!can && d.editable && card.dataset.secureUrl) {
      card.appendChild(el("p", {}, [el("a", { href: new URL("details.html#sources-editor", card.dataset.secureUrl).href,
        text: "Choose playlists on the secure page" }), el("span", { class: "muted", text: " (https://)." })]));
    }
    if (d.check && d.check.running) {
      var where = card.querySelector(".src-result:not([hidden])");
      if (where) poll(where);
    }
  }).catch(function () {});
})();
</script>
"""
