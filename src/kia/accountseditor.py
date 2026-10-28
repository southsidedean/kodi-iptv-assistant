"""The Accounts part of the Stack page: the front door's logins, and, on the secure page,
adding and removing them and setting who's an admin.

Like the stack chooser, kia can't do this itself: it asks the kia-stack service on the computer,
which holds the account store's admin password and runs scripts/frontdoor.sh (kia.stack,
docs/front-door.md). The answer comes a few seconds later, so the page asks kia for the accounts
again every two seconds until the change is done. The section shows only when the front door (the
frontdoor part) is on.
"""

STYLE = """
/* The Accounts section of the Stack page (with the shared form styles, editor.STYLE) */
#accounts-page { margin-top: var(--space-4); }
#accounts-page h2 { margin-top: 0; }
#accounts-page table { width: 100%; border-collapse: collapse; }
#accounts-page th, #accounts-page td { text-align: left; padding: .4rem .5rem; border-top: 1px solid var(--line); vertical-align: middle; }
#accounts-page th { color: var(--muted); font-weight: 600; font-size: var(--step--1); }
#accounts-page td.role { color: var(--muted); }
#accounts-page td.actions { text-align: right; white-space: nowrap; }
#accounts-page td.actions button { min-height: 40px; padding: .2rem .7rem; margin-left: var(--space-1); font-size: var(--step--1); }
@media (pointer: coarse) { #accounts-page td.actions button { min-height: 44px; } }
#accounts-page .add { border-top: 1px solid var(--line); margin-top: var(--space-3); padding-top: var(--space-3); }
#accounts-page .admin-field { display: flex; gap: var(--space-2); align-items: center; min-height: 44px; }
#accounts-page .admin-field input { width: 1.3rem; height: 1.3rem; }
"""

SCRIPT = r"""
<script>
(function () {
  var card = document.getElementById("accounts-page");
  if (!card) return;
  var body = card.querySelector(".body");
  var KEY = "kia-password";  // the same key as the other pages, so one password does for all
  var data = null, pass = null, polling = null;

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

  // Every control at once while a change is under way, so a second one can't race the first.
  function busy(on) { card.querySelectorAll("button, input").forEach(function (e) { e.disabled = on; }); }
  function say(text, bad) { card.status.className = "account-status " + (bad ? "error" : "preview"); card.status.textContent = text || ""; }

  function change(op, payload, doing) {
    busy(true);
    say(doing);
    api("POST", "accounts/" + op, payload).then(function (r) {
      if (r._status !== 200) {
        busy(false);
        say(r.error || "Something went wrong.", true);
        if (r._status === 401 && pass) { setPassword(""); pass.focus(); }
        return;
      }
      if (pass) setPassword(pass.value);
      wait(r.id, Date.now(), 2000);
    }, function () { busy(false); say(DOWN, true); });
  }

  // Ask for the accounts again every two seconds until this change's answer is there. The
  // service writes a fresh account list with the answer, so a redraw then shows it.
  function wait(id, since, every) {
    clearTimeout(polling);
    polling = setTimeout(function () {
      fetch("api/accounts", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d) { if (Date.now() - since > 300000) { say("No answer from kia for five minutes. Is kia running?", true); busy(false); } else wait(id, since, every); return; }
        var res = d.result;
        if (res && res.id === id && (res.state === "finished" || res.state === "refused")) {
          data = d;
          draw();
          if (res.state === "refused") say("The kia-stack service refused it: " + (res.output || "").trim(), true);
          else say(res.exit === "0" ? "Done." : "That didn't finish: " + (res.output || "").trim(), res.exit !== "0");
          return;
        }
        if (d.waiting !== id && !(res && res.id === id)) {
          say("The kia-stack service didn't pick the change up. Is it running on the computer?", true);
          busy(false);
          return;
        }
        if (Date.now() - since > 30000 && !d.service) say("The kia-stack service hasn't picked the change up. Is it running on the computer?", true);
        else say("Working…");
        wait(id, since, 2000);
      }, function () { if (Date.now() - since > 300000) { say(DOWN, true); busy(false); } else wait(id, since, every); });
    }, every);
  }

  // --- The accounts, and the actions on each one -----------------------------------
  function list() {
    var part = el("section", {}, [el("h2", { text: "Accounts" })]);
    if (!data.service) {
      part.appendChild(el("p", { class: "muted", text: "The accounts show here once the kia-stack service runs on this computer." }));
      return part;
    }
    if (!data.accounts.length) {
      part.appendChild(el("p", { class: "muted", text: "No one can log in yet. Add the first account below; it's an admin, so it can manage the rest." }));
      return part;
    }
    var canEdit = data.secure && data.editable;
    var rows = data.accounts.map(function (a) {
      var cells = [el("td", { text: a.name }), el("td", { class: "role", text: a.admin ? "admin" : "member" })];
      if (canEdit) {
        var toggle = el("button", { type: "button", text: a.admin ? "Make member" : "Make admin",
          on: { click: function () { change("set-admin", { name: a.name, admin: !a.admin }, "Changing " + a.name + "'s role…"); } } });
        var remove = el("button", { type: "button", text: "Remove",
          on: { click: function () { if (confirm("Remove the account " + a.name + "? They won't be able to log in.")) change("remove", { name: a.name }, "Removing " + a.name + "…"); } } });
        cells.push(el("td", { class: "actions" }, [toggle, remove]));
      }
      return el("tr", {}, cells);
    });
    var head = [el("th", { text: "Login" }), el("th", { text: "Role" })];
    if (canEdit) head.push(el("th", {}));
    part.appendChild(el("table", {}, [el("thead", {}, [el("tr", {}, head)]), el("tbody", {}, rows)]));
    return part;
  }

  // --- Adding an account (secure page, with the password) --------------------------
  function adder() {
    if (!data.secure) {
      var note = el("p", { class: "muted" });
      if (data.editable && card.dataset.secureUrl) {
        note.appendChild(el("a", { href: new URL("stack.html", card.dataset.secureUrl).href, text: "Add or change accounts on the secure page" }));
        note.appendChild(el("span", { text: " (https://), with kia's password. Or in the kia folder: " }));
      } else {
        note.appendChild(el("span", { text: "Add or change accounts in the kia folder: " }));
      }
      note.appendChild(el("code", { text: "scripts/frontdoor.sh account NAME" }));
      note.appendChild(el("span", { text: "." }));
      return note;
    }
    if (!data.editable) return el("p", { class: "muted", text: "Editing is off (KIA_EDITING=false), so add accounts with scripts/frontdoor.sh in the kia folder." });

    var box = el("div", { class: "add" }, [el("h3", { text: "Add an account" })]);
    pass = el("input", { type: "password", id: "account-pass", autocomplete: "current-password", value: getPassword() });
    box.appendChild(el("div", { class: "editor" }, [el("label", { class: "field", for: "account-pass" },
      [el("strong", { text: "kia's password" }), el("span", { class: "hint", text: "Needed to change accounts. This tab remembers it until you close it." })]), pass]));
    var name = el("input", { type: "text", id: "account-name", autocomplete: "off", spellcheck: "false", placeholder: "such as dad" });
    box.appendChild(el("div", { class: "editor" }, [el("label", { class: "field", for: "account-name" },
      [el("strong", { text: "Login name" }), el("span", { class: "hint", text: "Lowercase letters, digits, and dashes." })]), name]));
    var newpw = el("input", { type: "password", id: "account-newpw", autocomplete: "new-password" });
    box.appendChild(el("div", { class: "editor" }, [el("label", { class: "field", for: "account-newpw" },
      [el("strong", { text: "Their password" }), el("span", { class: "hint", text: "At least 8 characters. They can change it later." })]), newpw]));
    var admin = el("input", { type: "checkbox", id: "account-admin", checked: true });
    box.appendChild(el("label", { class: "admin-field", for: "account-admin" }, [admin, el("span", { text: "An admin (can manage the stack and other accounts)" })]));
    var add = el("button", { type: "button", class: "primary", text: "Add account",
      on: { click: function () {
        if (!name.value.trim() || !newpw.value) { say("A login name and a password are needed.", true); return; }
        change("add", { name: name.value.trim(), admin: admin.checked, password: newpw.value }, "Adding " + name.value.trim() + "…");
        newpw.value = "";
      } } });
    box.appendChild(el("div", { class: "actions" }, [add]));
    return box;
  }

  function draw() {
    body.textContent = "";
    body.appendChild(list());
    body.appendChild(adder());
    card.status = el("p", { role: "status", class: "account-status" });
    body.appendChild(card.status);
  }

  fetch("api/accounts", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    // Only with the front door on is there anything to manage; otherwise the section stays hidden.
    if (!d || !d.frontdoor) return;
    data = d; card.hidden = false; draw();
    if (data.waiting) { busy(true); wait(data.waiting, Date.now(), 0); }
  }, function () {});
})();
</script>
"""
