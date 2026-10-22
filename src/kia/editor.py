"""The Dashboard's profile editor: a form in the Playlists card that talks to kia.api.

The pages themselves stay plain files. This script asks /api/profiles what's there; when
there's no API (a page opened from disk, or a server without one) it does nothing. The home
page offers "Make a playlist" (#playlists has data-add); a playlist's own page offers Edit
(its row has data-profile). Saving rewrites the profiles file and the pages, then this
opens the playlist's page.
"""

STYLE = """
/* Profile editor */
.tools { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; margin-top: var(--space-3); }
.editor { display: grid; gap: var(--space-3); margin-top: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.editor h3 { margin: 0; font-size: var(--step-0); }
.editor fieldset { border: 1px solid var(--line); border-radius: var(--radius-small); margin: 0; padding: var(--space-2) var(--space-3); min-width: 0; }
.editor legend { font-weight: 600; padding: 0 .3rem; }
.editor label.field { display: grid; gap: .25rem; }
.editor input[type=text], .editor input[type=password], .editor textarea {
  background: var(--card); border: 1px solid var(--control); border-radius: var(--radius-small); color: var(--fg);
  font: inherit; padding: .55rem .7rem; width: 100%; min-height: 44px; }
.editor textarea { min-height: 4.5rem; font-family: var(--mono); font-size: .92em; }
.editor input:focus-visible, .editor textarea:focus-visible { outline: 2px solid var(--info); outline-offset: 2px; }
.editor .hint { color: var(--muted); font-size: var(--step--1); margin: 0; }
.check-filter { margin-top: var(--space-1); max-width: 18rem; }
.checks label { min-height: 40px; }
.checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr)); gap: .1rem var(--space-2);
  max-height: 14rem; overflow-y: auto; margin-top: var(--space-1); }
.checks label, .choice { display: flex; gap: .45rem; align-items: center; min-height: 36px; }
.checks input, .choice input { width: 1.1rem; height: 1.1rem; flex: none; }
.checks .n { color: var(--muted); font-size: var(--step--1); font-variant-numeric: tabular-nums; }
.chips { display: flex; flex-wrap: wrap; gap: var(--space-1); margin: var(--space-1) 0 0; padding: 0; list-style: none; }
.chips button, .found button { min-height: 36px; padding: .2rem .7rem; font-size: var(--step--1); }
.found { list-style: none; margin: var(--space-1) 0 0; padding: 0; display: grid; gap: .2rem; }
.preview { background: var(--info-bg); border-radius: var(--radius-small); padding: .6rem .8rem; margin: 0; }
.preview.warn { background: var(--warn-bg); }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
@media (pointer: coarse) { .checks label, .choice, .chips button, .found button { min-height: 44px; } }
button.primary { background: var(--info); border-color: var(--info); color: var(--card); font-weight: 600; }
button.danger { color: var(--bad); }
button:disabled { opacity: .55; cursor: default; }
.error { background: var(--bad-bg); color: var(--fg); border-radius: var(--radius-small); padding: .6rem .8rem; margin: 0; }
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("playlists");
  if (!card) return;
  var root = card.dataset.root || "";
  var KEY = "kia-password";
  var data = null;

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
  function api(method, path, body, password) {
    var headers = { "Content-Type": "application/json" };
    if (password) headers["X-Kia-Password"] = password;
    return fetch(root + "api/" + path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function lines(text) { return text.split("\n").map(function (s) { return s.trim(); }).filter(Boolean); }
  function plural(n, one, many) { return n.toLocaleString() + " " + (n === 1 ? one : many); }

  try {
    var note = sessionStorage.getItem("kia-saved");
    if (note) { sessionStorage.removeItem("kia-saved"); card.insertBefore(el("p", { class: "preview", role: "status", text: note }), card.firstChild.nextSibling); }
  } catch (e) {}

  api("GET", "profiles").then(function (d) {
    if (d._status !== 200 || !d.groups) return;  // no editor API here
    data = d;
    var tools = el("div", { class: "tools" });
    var adding = card.hasAttribute("data-add");
    if (d.editable && !d.secure) {
      // Changes go over HTTPS only; offer this page on the secure address.
      var to = card.dataset.secureUrl ? new URL(location.pathname.replace(/^\//, ""), card.dataset.secureUrl).href : null;
      var own = document.querySelector("#playlists [data-profile]");
      if (to) tools.appendChild(el("p", {}, [el("a", { href: to, text: adding ? "Make or change playlists on the secure page"
          : own ? "Change this playlist on the secure page" : "Star, number, and test channels on the secure page" }),
        el("span", { class: "muted", text: " (https://). Your browser may warn about kia's own certificate the first time." })]));
    } else if (d.editable) {
      if (adding) tools.appendChild(el("button", { type: "button", text: "Make a playlist", on: { click: function () { open(null); } } }));
      document.querySelectorAll("#playlists [data-profile]").forEach(function (row) {
        tools.appendChild(el("button", { type: "button", text: "Edit this playlist",
          on: { click: function () { open(row.dataset.profile); } } }));
      });
    } else if (!d.error && adding && d.password === "off") {
      tools.appendChild(el("p", { class: "muted", text: "Changing playlists here is turned off (KIA_EDITING in kia's .env file)." }));
    }
    card.appendChild(tools);
  }).catch(function () {});

  function open(name) {
    var opener = document.activeElement;  // Cancel brings focus back here
    var old = document.getElementById("editor");
    if (old) old.remove();
    var p = name ? data.profiles.filter(function (x) { return x.name === name; })[0] || { name: name } : {};
    var pinned = (p.channels || []).slice();
    var known = Object.assign({}, data.channel_names || {});
    var picking = !!((p.groups || []).length || (p.sources || []).length || (p.names || []).length);

    function checks(items, chosen, prefix) {
      var set = {};
      (chosen || []).forEach(function (g) { set[g.toLowerCase()] = true; });
      var box = el("div", { class: "checks" }, items.map(function (it) {
        return el("label", {}, [el("input", { type: "checkbox", value: it.name, "data-list": prefix, checked: !!set[it.name.toLowerCase()],
          on: { change: changed } }), it.name + " ", el("span", { class: "n", text: it.count.toLocaleString() })]);
      }));
      if (items.length < 12) return box;
      var filter = el("input", { type: "search", class: "check-filter", placeholder: "Filter these " + items.length,
        "aria-label": "Filter these " + items.length, on: { input: function () {
          var q = filter.value.toLowerCase();
          Array.prototype.forEach.call(box.children, function (l) { l.hidden = q && l.textContent.toLowerCase().indexOf(q) < 0; });
        } } });
      return el("div", {}, [filter, box]);
    }
    function chosen(list) {
      return Array.prototype.map.call(form.querySelectorAll('input[data-list="' + list + '"]:checked'), function (b) { return b.value; });
    }

    var nameBox = el("input", { type: "text", id: "ed-name", value: p.name || "", autocomplete: "off", autocapitalize: "none",
      spellcheck: "false", maxlength: "40", disabled: !!name, on: { input: function () {
        nameBox.value = nameBox.value.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+/, "");
        address.textContent = (nameBox.value || "name") + ".m3u"; } } });
    var address = el("code", { text: (p.name || "name") + ".m3u" });
    var titleBox = el("input", { type: "text", id: "ed-title", value: p.title || "", maxlength: "60", on: { input: changed } });
    var everyRadio = el("input", { type: "radio", name: "ed-start", value: "all", checked: !picking, on: { change: mode } });
    var pickRadio = el("input", { type: "radio", name: "ed-start", value: "pick", checked: picking, on: { change: mode } });
    var namesBox = el("textarea", { id: "ed-names", rows: "2", spellcheck: "false", on: { input: changed } });
    namesBox.value = (p.names || []).join("\n");
    var picks = el("div", { hidden: !picking }, [
      el("p", { class: "hint", text: "Channels in any of the categories or playlists you tick, or with any of the names below." }),
      el("h3", { text: "Categories" }), checks(data.groups, p.groups, "groups"),
      el("h3", { text: "From these channel sources" }), checks(data.sources, p.sources, "sources"),
      el("label", { class: "field", for: "ed-names" }, [el("strong", { text: "Channel names containing" }),
        el("span", { class: "hint", text: "One per line, e.g. news. Upper and lower case don't matter." })]), namesBox]);
    var exNamesBox = el("textarea", { id: "ed-exnames", rows: "2", spellcheck: "false", on: { input: changed } });
    exNamesBox.value = (p.exclude_names || []).join("\n");
    var tvBox = el("input", { type: "checkbox", checked: p.tv !== false, on: { change: changed } });
    var radioBox = el("input", { type: "checkbox", checked: p.radio !== false, on: { change: changed } });
    var renumberBox = el("input", { type: "checkbox", checked: p.renumber === true });
    var threadfinBox = el("input", { type: "checkbox", checked: p.threadfin === true });
    var search = el("input", { type: "search", placeholder: "Search channels by name", "aria-label": "Search channels by name",
      on: { input: debounce(find, 250) } });
    var found = el("ul", { class: "found" });
    var chips = el("ul", { class: "chips" });
    var preview = el("p", { class: "preview", "aria-live": "polite", text: "Counting…" });
    var error = el("p", { class: "error", hidden: true, role: "alert" });
    var pass = el("input", { type: "password", id: "ed-pass", value: getPassword(), autocomplete: "current-password",
      on: { change: function () { changed(); } } });
    var save = el("button", { type: "submit", class: "primary", text: "Save" });

    var form = el("form", { class: "editor", id: "editor", on: { submit: submit } }, [
      el("h3", { text: name ? "Edit " + (p.title || name) : "Make a playlist" }),
      el("label", { class: "field", for: "ed-name" }, [el("strong", { text: "Short name" }),
        el("span", { class: "hint" }, ["Lowercase letters, numbers, and dashes. The playlist's address ends in ", address,
          name ? ". To rename a playlist, add a new one and delete this one." : "."])]), nameBox,
      el("label", { class: "field", for: "ed-title" }, [el("strong", { text: "Title" }),
        el("span", { class: "hint", text: "Shown on this page, e.g. The kids' tablet. Optional." })]), titleBox,
      el("fieldset", {}, [el("legend", { text: "Start with" }),
        el("label", { class: "choice" }, [everyRadio, "Every channel"]),
        el("label", { class: "choice" }, [pickRadio, "Only the channels I pick"]), picks]),
      el("fieldset", {}, [el("legend", { text: "Leave out" }),
        el("h3", { text: "Categories" }), checks(data.groups, p.exclude_groups, "exclude_groups"),
        el("label", { class: "field", for: "ed-exnames" }, [el("strong", { text: "Channel names containing" }),
          el("span", { class: "hint", text: "One per line." })]), exNamesBox,
        el("label", { class: "choice" }, [tvBox, "TV channels"]),
        el("label", { class: "choice" }, [radioBox, "Radio stations"]),
        el("p", { class: "hint", text: "Untick TV or radio to leave all of those out." })]),
      el("fieldset", {}, [el("legend", { text: "Channel numbers" }),
        el("label", { class: "choice" }, [renumberBox, "Number this playlist's channels 1, 2, 3…"]),
        el("p", { class: "hint", text: "Off: each channel keeps its number from the full list, so it's the same on every device." })]),
      el("fieldset", {}, [el("legend", { text: "Plex" }),
        el("label", { class: "choice" }, [threadfinBox, "For Plex through Threadfin"]),
        el("p", { class: "hint", text: "Writes \u2019 for ' in channel names, which Threadfin needs to read them right." })]),
      el("fieldset", {}, [el("legend", { text: "Always include" }),
        el("p", { class: "hint", text: "Specific channels, whatever the settings above say." }), search, found, chips]),
      preview,
      el("label", { class: "field", for: "ed-pass" }, [el("strong", { text: "Password" }),
        el("span", { class: "hint", text: ({ env: "The one set by KIA_ADMIN_PASSWORD in kia's .env file.",
          generated: "kia made one: it's in state/admin-password.txt in the kia folder.",
          set: "The one set on the System page." }[data.password] || "kia's admin password.") + " This browser tab remembers it until you close it." })]), pass,
      error,
      el("div", { class: "actions" }, [save,
        el("button", { type: "button", text: "Cancel", on: { click: function () {
          form.remove();
          if (opener && opener.isConnected) opener.focus();
        } } }),
        name ? el("button", { type: "button", class: "danger", text: "Delete playlist", on: { click: remove } }) : null])]);

    function mode() { picks.hidden = !pickRadio.checked; changed(); }
    function settings() {
      var s = {};
      if (titleBox.value.trim()) s.title = titleBox.value.trim();
      if (pickRadio.checked) {
        s.groups = chosen("groups"); s.sources = chosen("sources"); s.names = lines(namesBox.value);
      }
      s.channels = pinned.slice();
      s.exclude_groups = chosen("exclude_groups"); s.exclude_names = lines(exNamesBox.value);
      s.exclude_channels = (p.exclude_channels || []).slice(); // set in the profiles file; kept as it is
      Object.keys(s).forEach(function (k) { if (Array.isArray(s[k]) && !s[k].length) delete s[k]; });
      if (!tvBox.checked) s.tv = false;
      if (!radioBox.checked) s.radio = false;
      if (renumberBox.checked) s.renumber = true;
      if (threadfinBox.checked) s.threadfin = true;
      return s;
    }
    function drawChips() {
      chips.textContent = "";
      pinned.forEach(function (id) {
        chips.appendChild(el("li", {}, [el("button", { type: "button", "aria-label": "Remove " + (known[id] || id),
          text: (known[id] || id) + " ✕", on: { click: function () { pinned.splice(pinned.indexOf(id), 1); drawChips(); changed(); } } })]));
      });
    }
    var asking = 0;  // only the newest search's answer is shown (an older one can arrive later)
    function find() {
      var q = search.value.trim(), mine = ++asking;
      found.textContent = "";
      if (q.length < 2) return;
      api("GET", "channels?q=" + encodeURIComponent(q)).then(function (r) {
        if (mine !== asking) return;
        if (r._status && r._status !== 200) { found.appendChild(el("li", { class: "muted", text: r.error || "Couldn't search just now." })); return; }
        (r.channels || []).forEach(function (c) {
          known[c.id] = c.name;
          if (pinned.indexOf(c.id) >= 0) return;
          found.appendChild(el("li", {}, [el("button", { type: "button", text: "Add", "aria-label": "Add " + c.name,
            on: { click: function () { pinned.push(c.id); drawChips(); changed(); find(); } } }),
            " " + c.name + " ", el("span", { class: "muted", text: c.group || "" })]));
        });
      }, function () { if (mine === asking) found.appendChild(el("li", { class: "muted", text: "kia didn't answer. Is it running?" })); });
    }
    var counting = 0, lastCount = null;
    function changed() { counting++; later(); }
    var later = debounce(function () {
      var mine = counting;
      var s = settings();
      api("POST", "preview", s, pass.value || getPassword()).then(function (r) {
        if (mine !== counting) return;
        if (r._status === 401 && (s.names || s.exclude_names)) { preview.className = "preview warn";
          preview.textContent = "Enter the password below to count channels by name."; return; }
        if (r._status !== 200) { preview.className = "preview warn"; preview.textContent = r.error || "Can't count channels."; return; }
        var empty = pickRadio.checked && !s.groups && !s.sources && !s.names;
        preview.className = "preview" + (empty || !(r.tv + r.radio) ? " warn" : "");
        preview.textContent = (empty ? "Pick some categories, playlists, or names; until then this has every channel. " : "")
          + "This playlist would have " + plural(r.tv, "TV channel", "TV channels") + " and "
          + plural(r.radio, "radio station", "radio stations") + (r.sample.length ? ", such as " + r.sample.slice(0, 5).join(", ") : "") + ".";
        lastCount = r.tv + r.radio;
      }, function () { if (mine === counting) { preview.className = "preview warn"; preview.textContent = "Can't count channels right now: kia didn't answer."; } });
    }, 300);
    function fail(r) {
      error.hidden = false;
      error.textContent = r.error || "Something went wrong.";
      if (r._status === 401) { setPassword(""); pass.focus(); }
      save.disabled = false; save.textContent = "Save";
    }
    function submit(ev) {
      ev.preventDefault();
      error.hidden = true;
      var n = nameBox.value.replace(/^-+|-+$/g, "");
      if (!n) { fail({ error: "Give the playlist a short name." }); nameBox.focus(); return; }
      if (!name && data.profiles.some(function (x) { return x.name === n; })) {
        fail({ error: "There's already a playlist named " + n + "." }); return; }
      nameBox.value = n;
      if (lastCount === 0 && !confirm("No channels match these settings, so this playlist would be empty. Save it anyway?")) return;
      save.disabled = true; save.textContent = "Saving…";
      api("PUT", "profiles/" + encodeURIComponent(n), settings(), pass.value).then(function (r) {
        if (r._status !== 200) return fail(r);
        setPassword(pass.value);
        try { sessionStorage.setItem("kia-saved", name ? "Saved." : "Made the playlist. Its address is below."); } catch (e) {}
        if (name) location.reload(); else location.href = root + "playlists/" + n + ".html";
      }, function () { fail({ error: "kia didn't answer. Is it running?" }); });
    }
    var deleting = false;  // one Delete at a time
    function remove() {
      if (deleting) return;
      if (!confirm("Delete the " + (p.title || name) + " playlist? Devices using its address will stop getting channels.")) return;
      deleting = true;
      api("DELETE", "profiles/" + encodeURIComponent(name), null, pass.value).then(function (r) {
        deleting = false;
        if (r._status !== 200) return fail(r);
        setPassword(pass.value);
        try { sessionStorage.setItem("kia-saved", "Deleted the " + (p.title || name) + " playlist."); } catch (e) {}
        location.href = root + "playlists.html";
      }, function () { deleting = false; fail({ error: "kia didn't answer. Is it running?" }); });
    }

    drawChips();
    card.appendChild(form);
    changed();
    (name ? titleBox : nameBox).focus();
    form.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }
})();
</script>
"""

EDITING_SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("editing");
  if (!card) return;
  var note = card.querySelector("p");
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k]; else if (attrs[k] !== false && attrs[k] != null) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function say(text) { note.className = "muted"; note.textContent = text; }
  fetch("api/profiles").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d) { say(location.protocol === "file:" ? "Open this page through kia's address to make changes." : "Editing isn't available from this server."); return; }
    var where = { env: "The password is set by KIA_ADMIN_PASSWORD in kia's .env file. Change it there and restart kia.",
      generated: "kia made the password. It's in state/admin-password.txt in the kia folder until you change it here.",
      set: "The password was set on this page.",
      off: "Changes from the Dashboard are turned off (KIA_EDITING=false in kia's .env file)." }[d.password] || "";
    say(where);
    if (d.password !== "env" && d.password !== "off") {
      card.appendChild(el("p", { class: "muted" }, ["Forgot it? On the kia computer, in the kia folder, run ",
        el("code", { text: "docker compose exec kia python3 -m kia password --reset" }),
        " (without Docker: ", el("code", { text: "PYTHONPATH=src python3 -m kia password --reset" }), "), then read state/admin-password.txt. ",
        el("a", { href: "https://github.com/southsidedean/kodi-iptv-assistant/blob/main/docs/secure-page.md#if-you-forget-it", text: "More" })]));
    }
    if (d.password === "env" || d.password === "off") return;
    if (!d.secure) {
      if (card.dataset.secureUrl) card.appendChild(el("p", {}, [el("a", { href: new URL("system.html", card.dataset.secureUrl).href, text: "Change the password on the secure page" })]));
      return;
    }
    var current = el("input", { type: "password", id: "pw-current", autocomplete: "current-password", class: "pw" });
    var fresh = el("input", { type: "password", id: "pw-new", autocomplete: "new-password", minlength: "8", class: "pw" });
    var again = el("input", { type: "password", id: "pw-again", autocomplete: "new-password", class: "pw" });
    var msg = el("p", { role: "status", hidden: true });
    var button = el("button", { type: "submit", class: "primary", text: "Change password" });
    var form = el("form", { class: "editor" }, [
      el("label", { class: "field", for: "pw-current" }, [el("strong", { text: "Current password" })]), current,
      el("label", { class: "field", for: "pw-new" }, [el("strong", { text: "New password" }),
        el("span", { class: "hint", text: "At least 8 characters. Everyone who changes playlists will need it." })]), fresh,
      el("label", { class: "field", for: "pw-again" }, [el("strong", { text: "New password again" })]), again,
      msg, el("div", { class: "actions" }, [button])]);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      function show(text, bad) { msg.hidden = false; msg.className = bad ? "error" : "preview"; msg.textContent = text; }
      if (fresh.value !== again.value) { again.focus(); return show("The new passwords don't match.", true); }
      button.disabled = true;
      fetch("api/password", { method: "POST", headers: { "Content-Type": "application/json", "X-Kia-Password": current.value },
        body: JSON.stringify({ new: fresh.value }) })
        .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); })
        .then(function (res) {
          button.disabled = false;
          if (res[0] === 401) { current.focus(); return show("That's not the current password. Forgot it? See above.", true); }
          if (res[0] !== 200) return show(res[1].error || "Something went wrong.", true);
          try { sessionStorage.removeItem("kia-password"); } catch (e) {}
          current.value = fresh.value = again.value = "";
          say("The password was set on this page.");
          show("Password changed.", false);
        }, function () { button.disabled = false; show("kia didn't answer. Is it running?", true); });
    });
    card.appendChild(form);
  }).catch(function () { say("Editing isn't available from this server."); });
})();
</script>
"""
