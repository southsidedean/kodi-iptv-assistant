"""A status page (index.html) and status.json in the output folder.

They answer "is kia working?" from any browser on the network: when it last ran, whether
that run worked, what it found, and any alerts it raised. kia rewrites both whenever a
run starts or ends and whenever the playlist or guide changes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import escape
from pathlib import Path

from kia import __version__, channeleditor, editor, guideview, sourceeditor
from kia.htmlpage import FILTER_ROWS, FILTER_SCRIPT, page
from kia.history import readable
from kia.m3u import parse

# Files in output/ the page links to, with what each one is for.
FILES = [
    ("curated.m3u", "Playlist for Kodi (IPTV Simple Client's M3U playlist URL)"),
    ("guide.xml.gz", "Program guide (found through the playlist; no Kodi setting needed)"),
    ("report.html", "Stream check: working, flaky, and dead streams"),
    ("guide-report.html", "Guide coverage by channel and site"),
    ("mapping-report.html", "Which guide sites match which channels, including matches by name to check"),
    ("pluto.json", "Pluto TV channels playing through kia right now"),
]
PROJECT_URL = "https://github.com/southsidedean/kodi-iptv-assistant"
DOCS_URL = f"{PROJECT_URL}/blob/main/docs"
# Docs pages for someone looking at this page because something seems wrong.
HELP = [
    (f"{DOCS_URL}/troubleshooting.md", "Troubleshooting", "no channels, no guide, failed refreshes, alerts"),
    (f"{DOCS_URL}/kodi.md", "Setting up Kodi", "the playlist, repeated refresh, and channel numbers"),
    (f"{DOCS_URL}/players.md", "Other players", "Plex, Jellyfin, Emby, TiviMate, VLC, phones and tablets"),
    (f"{DOCS_URL}/configuration.md", "Settings", "playlists, refresh time, and when changes take effect"),
    (f"{DOCS_URL}/how-it-works.md", "How it works", "what each nightly step does, and the reports"),
    (f"{DOCS_URL}/alerts.md", "Alerts", "what each alert means, and getting them on your phone"),
    (f"{DOCS_URL}/secure-page.md", "The secure page", "the password, the browser warning, and using your own certificate"),
    (f"{DOCS_URL}/api.md", "API", "using kia from scripts and other apps"),
]
RESULTS = {
    "running": "Refreshing now",
    "ok": "Everything's working",
    "alerts": "Working, with alerts",
    "failed": "The last refresh failed",
    "never": "Waiting for the first refresh",
}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def update(output_dir: Path, state_dir: Path, public_url: str | None = None, **run: object) -> dict:
    """Merge `run` fields (result, started, finished, minutes, exit_code, next_run; None
    clears one) into the saved run state, then rewrite status.json and index.html."""
    with _UPDATE_LOCK:  # the daemon and the page's API both update it
        return _update(output_dir, state_dir, public_url, run)


_UPDATE_LOCK = __import__("threading").RLock()


def _update(output_dir: Path, state_dir: Path, public_url: str | None, run: dict) -> dict:
    saved_path = state_dir / "run.json"
    saved = _load(saved_path)
    saved.update(run)
    _write(saved_path, json.dumps(saved, indent=1, sort_keys=True))
    status = collect(output_dir, state_dir, saved, public_url)
    _write(output_dir / "status.json", json.dumps(status, indent=1))
    write_pages(output_dir, status)
    return status


def collect(output_dir: Path, state_dir: Path, run: dict, public_url: str | None) -> dict:
    playlist = output_dir / "curated.m3u"
    entries = parse(playlist.read_text()) if playlist.exists() else []
    radio = sum(1 for e in entries if e.is_radio)
    counts = _load(state_dir / "alert-state.json")
    return {
        "version": __version__,
        "result": run.get("result", "never"),
        "last_run": {k: run.get(k) for k in ("started", "finished", "minutes", "exit_code")},
        "next_run": run.get("next_run"),
        "guide_updated": run.get("guide_updated"),
        "channels": {
            "tv": len(entries) - radio,
            "radio": radio,
            "with_guide": counts.get("guide_channels"),
        },
        "coverage": run.get("coverage"),
        "sources": source_counts(output_dir / "report.json"),
        "alerts": recent_alerts(state_dir / "alerts.log", run.get("started")),
        "playlist_url": f"{public_url}/curated.m3u" if public_url else None,
        "profiles": _profiles(output_dir, public_url),
        "profiles_error": _load(output_dir / "profiles.json").get("error"),
        "secure": _load(state_dir / "secure.json"),
        "settings": _load(state_dir / "settings.json").get("sections", []),
        "pluto_channels": _pluto_channels(entries),
        "files": [n for name, _ in FILES for n in (name, markdown_twin(name)) if n and (output_dir / n).exists()],
    }


def _pluto_channels(entries: list) -> dict[str, str]:
    """Pluto channel id -> name, for channels the playlist plays through kia."""
    out = {}
    for e in entries:
        m = re.search(r"/pluto/([0-9a-f]{24})\.m3u8$", e.url)
        if m:
            out[m.group(1)] = e.name
    return out


def _profiles(output_dir: Path, public_url: str | None) -> list[dict]:
    """Each profile's playlist (see kia.profiles), with its address when kia knows it."""
    found = _load(output_dir / "profiles.json").get("profiles", [])
    for p in found:
        p["url"] = f"{public_url}/{p['file']}" if public_url else None
    return found


def source_counts(report: Path) -> list[dict]:
    """Working, flaky, and dead streams per playlist, from the last check's report."""
    streams = _load(report).get("streams", [])
    counts: dict[str, dict] = {}
    for s in streams:
        c = counts.setdefault(s.get("source") or "?", {"name": s.get("source") or "?", "ok": 0, "flaky": 0, "dead": 0})
        if s.get("status") in ("ok", "flaky", "dead"):
            c[s["status"]] += 1
    return sorted(counts.values(), key=lambda c: -(c["ok"] + c["flaky"] + c["dead"]))


def markdown_twin(name: str) -> str | None:
    """report.html -> report.md: each HTML report is made from a Markdown one."""
    return name.removesuffix(".html") + ".md" if name.endswith(".html") else None


def recent_alerts(log: Path, since: str | None, limit: int = 20) -> list[dict]:
    """Alerts logged since the last run started (alerts.log: time, event, title, message)."""
    if not since or not log.exists():
        return []
    found = []
    for line in log.read_text(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] >= since:
            event, title = (parts[1], parts[2]) if len(parts) >= 4 else ("", parts[1])
            found.append({"time": parts[0], "event": event, "title": title})
    return found[-limit:]


def _when(iso: str | None) -> str:
    return readable(iso)


COPY_SCRIPT = """
<script>
document.querySelectorAll("button[data-copy]").forEach(function (b) {
  b.addEventListener("click", function () {
    var text = document.getElementById(b.dataset.copy).textContent;
    var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1500); };
    var select = function () { var r = document.createRange(); r.selectNodeContents(document.getElementById(b.dataset.copy));
      var s = getSelection(); s.removeAllRanges(); s.addRange(r); };
    if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done, select); } else { select(); }
  });
});
</script>
"""


def _n(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else "-"


def _bar(parts: list[tuple[int, str]]) -> str:
    total = sum(n for n, _ in parts) or 1
    return '<div class="bar">' + "".join(
        f'<span style="width:{100 * n / total:.2f}%;background:var({color})"></span>' for n, color in parts if n
    ) + "</div>"


def _banner(s: dict) -> str:
    run = s["last_run"]
    facts = []
    if s["result"] == "running":
        facts.append(f"Started {_when(run['started'])}.")
    elif run.get("started"):
        took = f", took {run['minutes']} min" if run.get("minutes") is not None else ""
        facts.append(f"Last refresh {_when(run['started'])}{took}.")
    if s.get("guide_updated"):
        facts.append(f"Guide updated {_when(s['guide_updated'])}.")
    if s.get("next_run"):
        facts.append(f"Next nightly run {_when(s['next_run'])}.")
    if s["result"] == "alerts":
        facts.append("The alerts are listed below.")
    title = RESULTS.get(s["result"], s["result"])
    return (
        f'<section class="banner state-{escape(s["result"])}"><span class="dot"></span>'
        f"<div><h1>{escape(title, quote=False)}</h1><p>{escape(' '.join(facts), quote=False)}</p></div></section>"
    )


def _playlists(s: dict) -> list[dict]:
    """Everything, then each profile: title, files, counts, address, and its page."""
    ch = s["channels"]
    base = s["playlist_url"].removesuffix("/curated.m3u") if s.get("playlist_url") else None
    out = [{"name": None, "title": "Everything", "file": "curated.m3u", "guide": "guide.xml.gz",
            "tv": ch["tv"] or 0, "radio": ch["radio"] or 0, "page": "playlists/curated.html"}]  # fmt: skip
    for p in s.get("profiles", []):
        out.append({"name": p["name"], "title": p["title"], "file": p["file"], "guide": p.get("guide") or "guide.xml.gz",
                    "tv": p["tv"], "radio": p["radio"], "page": f"playlists/{p['name']}.html",
                    "builtin": bool(p.get("builtin"))})  # fmt: skip
    for pl in out:
        pl["url"] = f"{base}/{pl['file']}" if base else None
        pl["guide_url"] = f"{base}/{pl['guide']}" if base else None
    return out


def _counts(pl: dict) -> str:
    return f"{_n(pl['tv'])} TV channels" + (f", {_n(pl['radio'])} radio stations" if pl["radio"] else "")


def _address(i: int | str, url: str | None, file: str, root: str = "", what: str = "address") -> str:
    if not url:
        return f'<a href="{escape(root + file)}">{escape(file)}</a>'
    return (
        f'<div class="copy"><code id="copy-{i}">{escape(url)}</code>'
        f'<button type="button" data-copy="copy-{i}" aria-label="Copy the {escape(what)}">Copy</button></div>'
    )


def _profiles_error(s: dict) -> str:
    if not s.get("profiles_error"):
        return ""
    return (
        "<p class=\"bad-note\">The profiles file has a mistake, so the profile playlists weren't updated: "
        f"{escape(s['profiles_error'])}</p>"
    )


def _playlists_card(s: dict) -> str:
    """Every playlist with its address, a Copy button, and a link to its page."""
    items = []
    for i, pl in enumerate(_playlists(s)):
        items.append(
            f'<div class="playlist"><div><a class="pl-title" href="{escape(pl["page"])}">{escape(pl["title"])}</a> '
            f'<span class="muted">{_counts(pl)}</span></div>{_address(i, pl["url"], pl["file"], what=pl["title"] + " playlist address")}</div>'
        )
    return (
        f'<section class="card" id="playlists" data-add{_secure_attr(s)}><h2>Playlists</h2>'
        "<p>Use one of these as the <strong>M3U playlist URL</strong> in IPTV Simple Client or your player. "
        "Open one to see its channels.</p>"
        f'{_profiles_error(s)}<div class="playlists">{"".join(items)}</div></section>'
    )


def _day(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone()
    return f"{d:%b} {d.day}, {d.year}"


def _secure_attr(s: dict) -> str:
    """The secure page's address, for the editor's "open the secure page" link."""
    url = (s.get("secure") or {}).get("url")
    return f' data-secure-url="{escape(url)}"' if url else ""


def _secure_card(s: dict) -> str:
    secure = s.get("secure") or {}
    cert = secure.get("tls")
    if not cert:
        return (
            '<section class="card"><h2>Secure status page</h2><p class="bad-note">There\'s no certificate, so the '
            "secure page isn't working. kia's log says why.</p></section>"
        )
    url = secure.get("url") or ""
    made_by = "kia (self-signed, renewed automatically)" if cert.get("generated") else cert.get("issuer", "")
    expires = cert.get("expires")
    notes = []
    if cert.get("key_matches") is False:
        notes.append("The key in tls/privkey.pem doesn't match the certificate, so the secure page won't work.")
    if expires and not cert.get("generated"):
        left = (datetime.fromisoformat(expires) - datetime.now(datetime.fromisoformat(expires).tzinfo)).days
        if left < 21:
            notes.append(f"The certificate expires in {max(left, 0)} days. Replace the files in tls/ with a new one.")
    rows = "".join(f"<li>{escape(k)}<span>{escape(v)}</span></li>" for k, v in [
        ("Address", url), ("Certificate made by", made_by), ("Covers", ", ".join(cert.get("names", []))),
        ("Expires", _day(expires) if expires else "-")])  # fmt: skip
    return (
        '<section class="card"><h2>Secure status page</h2>'
        "<p>Changes are made on the secure page. Your browser warns about kia's own certificate the first time; "
        f'<a href="{escape(DOCS_URL)}/secure-page.md">the secure page guide</a> explains why and how to use your own.</p>'
        + "".join(f'<p class="bad-note">{escape(n)}</p>' for n in notes)
        + f'<ul class="links">{rows}</ul></section>'
    )


def _settings_card(s: dict) -> str:
    """The settings kia is running with (kia.settings), read only."""
    if not s.get("settings"):
        return ""
    groups = "".join(
        f"<h3>{escape(sec['title'])}</h3><table class=\"settings\"><tbody>"
        + "".join(
            f"<tr><td>{escape(r['name'])}</td><td>{escape(r['value'])}</td><td class=\"muted\">{escape(r['change'])}</td></tr>"
            for r in sec["rows"]
        )
        + "</tbody></table>"
        for sec in s["settings"]
    )
    return (
        '<section class="card" id="settings"><h2>Settings</h2>'
        "<p>What kia is running with, and where to change each setting: in <code>.env</code> or "
        "<code>kia.toml</code> in the kia folder, then restart kia. "
        f'<a href="{escape(DOCS_URL)}/configuration.md">Configuration</a> explains them all. '
        "Passwords and alert addresses only show whether they're set.</p>"
        f'<div class="wide">{groups}</div></section>'
    )


def _setting(s: dict, name: str) -> str | None:
    return next((r["value"] for sec in s.get("settings", []) for r in sec["rows"] if r["name"] == name), None)


def checklist(s: dict) -> list[dict]:
    """First steps for a new kia, for the home page. kia ticks what it can check itself
    ("done"); the person ticks the rest in their browser ("manual")."""
    url = s.get("playlist_url") or ""
    if "://my-server.lan" in url:
        url = ""  # the example from older .env files, never changed
    local_only = "://localhost" in url or "://127.0.0.1" in url
    alerts = any(_setting(s, n) == "set" for n in ("ntfy address", "Email", "Alert command"))
    password = _setting(s, "Password for changes")
    return [
        {"id": "refresh", "title": "kia finishes its first refresh", "done": bool(s["last_run"].get("finished")),
         "text": "It checks every stream and builds the guide. It takes up to an hour, and this page updates when it's done."},
        {"id": "address", "title": "Other devices can reach kia", "done": bool(url) and not local_only, "manual": local_only,
         "text": "This computer only (KIA_PUBLIC_URL is localhost). That's right if you watch on this computer; "
                 "for other devices, set KIA_PUBLIC_URL in .env to this computer's network address." if local_only
                 else "Set KIA_PUBLIC_URL in .env to this computer's address, e.g. http://192.168.1.20:8780, "
                      "so players find the guide.", "doc": "install.md#5-configure-kia"},
        {"id": "player", "title": "Set up a player on each device", "manual": True,
         "text": "Kodi, or another app: use a playlist address from below.", "doc": "players.md"},
        {"id": "password", "title": "Keep the password for changes safe",
         "done": password is not None and not password.startswith("made by kia"), "manual": True,
         "text": "kia made one, in state/admin-password.txt. Change it on the secure page's Details page, "
                 "or set KIA_ADMIN_PASSWORD in .env.", "doc": "secure-page.md#the-password"},
        {"id": "playlists", "title": "Choose your playlists (optional)", "manual": True,
         "text": "Add other countries, streaming services, or radio on the secure page's Details page.",
         "doc": "configuration.md#choosing-channel-sources-on-the-status-page"},
        {"id": "alerts", "title": "Get alerts when something breaks (optional)", "done": alerts,
         "text": "A message on your phone or by email if the nightly refresh fails.", "doc": "alerts.md"},
    ]  # fmt: skip


def _checklist_card(s: dict) -> str:
    items = checklist(s)
    if not s.get("settings") or all(i.get("done") for i in items):
        return ""
    rows = "".join(
        f'<li data-id="{i["id"]}"{" data-done" if i.get("done") else ""}{" data-manual" if i.get("manual") else ""}>'
        f'<span class="tick" aria-hidden="true">{"✓" if i.get("done") else ""}</span><div><strong>{escape(i["title"])}</strong>'
        f'<span>{escape(i["text"])}'
        + (f' <a href="{escape(DOCS_URL)}/{escape(i["doc"])}">How</a>' if i.get("doc") else "")
        + "</span></div></li>"
        for i in items
    )
    return (
        '<section class="card" id="getting-started"><h2>Getting started</h2>'
        '<p class="muted">kia ticks what it can check. Tick the rest yourself; this browser remembers. '
        "This card goes away when everything's ticked, or when you hide it.</p>"
        f'<ol class="steps">{rows}</ol><div class="actions"><button type="button" class="hide">Hide this</button></div></section>'
    )


CHECKLIST_STYLE = """
.steps { list-style: none; margin: var(--space-2) 0; padding: 0; display: grid; gap: var(--space-2); }
.steps li { display: grid; grid-template-columns: 1.8rem 1fr; gap: var(--space-2); align-items: start; }
.steps li div { display: grid; gap: .15rem; }
.steps li div span { color: var(--muted); font-size: var(--step--1); }
.steps .tick { width: 1.5rem; height: 1.5rem; border: 2px solid var(--line); border-radius: 50%; display: grid; place-items: center;
  font-weight: 700; color: var(--card); background: none; padding: 0; min-height: 0; }
.steps li[data-done] .tick { background: var(--good); border-color: var(--good); }
.steps li[data-done] strong { text-decoration: line-through; color: var(--muted); }
.steps button.tick { cursor: pointer; }
"""

CHECKLIST_SCRIPT = """
<script>
(function () {
  var card = document.getElementById("getting-started");
  if (!card) return;
  var KEY = "kia-getting-started";
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } }
  function save(v) { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) {} }
  var state = load();
  var again = document.querySelector("a.show-steps");
  if (again) again.addEventListener("click", function (ev) {
    ev.preventDefault(); state.hidden = false; save(state); card.hidden = false; again.hidden = true;
    card.scrollIntoView({ block: "start" });
  });
  if (state.hidden) { card.hidden = true; if (again) again.hidden = false; }
  function allDone() { return !card.querySelector("li:not([data-done])"); }
  card.querySelectorAll("li[data-manual]").forEach(function (li) {
    var old = li.querySelector(".tick"), b = document.createElement("button");
    b.type = "button"; b.className = "tick"; b.setAttribute("aria-label", "Done: " + li.querySelector("strong").textContent);
    function draw() { var on = li.hasAttribute("data-done"); b.textContent = on ? "✓" : ""; b.setAttribute("aria-pressed", on ? "true" : "false"); }
    if (state[li.dataset.id]) li.setAttribute("data-done", "");
    b.addEventListener("click", function () {
      if (li.hasAttribute("data-done")) { li.removeAttribute("data-done"); delete state[li.dataset.id]; }
      else { li.setAttribute("data-done", ""); state[li.dataset.id] = true; }
      save(state); draw();
      if (allDone()) card.hidden = true;
    });
    old.replaceWith(b); draw();
  });
  if (allDone()) card.hidden = true;
  card.querySelector("button.hide").addEventListener("click", function () {
    state.hidden = true; save(state); card.hidden = true; if (again) again.hidden = false; });
})();
</script>
"""


def _pluto_card(s: dict) -> str:
    """Pluto TV channels playing through kia right now (kia.pluto); filled in from pluto.json."""
    names = s.get("pluto_channels") or {}
    if not names:
        return ""
    return (
        f'<section class="card" id="pluto" data-names="{escape(json.dumps(names))}"><h2>Pluto TV</h2>'
        f"<p>Your playlists have {len(names):,} Pluto TV channels. They play through kia, which keeps them going "
        "across ad breaks. Channels show up here while someone's watching them. "
        f'<a href="{escape(DOCS_URL)}/troubleshooting.md#pluto-tv-channels-freeze-or-jump-at-ad-breaks">More about this</a>.</p>'
        '<p class="pluto-note bad-note" hidden></p>'
        '<p class="pluto-idle muted">Nothing playing right now.</p>'
        '<div class="wide"><table class="settings pluto-table" hidden><thead><tr><th>Channel</th><th>Session</th>'
        "<th>Today</th></tr></thead><tbody></tbody></table></div>"
        '<h3 class="pluto-problems-title" hidden>Recent problems</h3><ul class="alerts pluto-problems" hidden></ul>'
        "</section>"
    )


def _pluto_warning(s: dict) -> str:
    """A line on the home page, shown (by PLUTO_SCRIPT) only when kia's fix isn't working."""
    if not s.get("pluto_channels"):
        return ""
    return (
        '<section class="card" id="pluto-warning" hidden><p class="bad-note"></p></section>'
    )


PLUTO_SCRIPT = """
<script>
(function () {
  var card = document.getElementById("pluto"), warn = document.getElementById("pluto-warning");
  if (!card && !warn) return;
  var names = card ? JSON.parse(card.dataset.names || "{}") : {};
  function el(tag, text) { var e = document.createElement(tag); e.textContent = text; return e; }
  function when(iso) { var d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], {weekday: "short", hour: "numeric", minute: "2-digit"}); }
  fetch("pluto.json", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (p) {
    if (!p) return;
    var trouble = p.suspicious ? "Pluto has flagged kia's sessions as suspicious, so Pluto channels may misbehave."
      : p.fallback ? "Pluto channels are playing without kia's fix, so they may freeze at ad breaks. kia keeps trying."
      : "";
    if (warn && trouble) { warn.querySelector("p").textContent = trouble + " See Details."; warn.hidden = false; }
    if (!card) return;
    var note = card.querySelector(".pluto-note");
    if (trouble) { note.textContent = trouble; note.hidden = false; }
    var rows = card.querySelector("tbody"), problems = card.querySelector(".pluto-problems");
    (p.channels || []).forEach(function (c) {
      var tr = document.createElement("tr");
      var who = c.viewers === 1 ? "1 viewer" : c.viewers + " viewers";
      tr.appendChild(el("td", (names[c.id] || c.id) + " (" + who + ")"));
      var session = !c.own_session ? "plain jmp2.uk link for " + c.fallback_minutes + " min"
        : "kia's own, " + c.session_hours + " h old" + (c.next_refresh_hours !== null ? ", fresh one in " + c.next_refresh_hours + " h" : "");
      tr.appendChild(el("td", session + (c.suspicious ? ", flagged by Pluto" : "")));
      tr.appendChild(el("td", c.rewinds + " rewinds caught, " + c.sessions + " sessions, " + c.errors + " errors, " + c.fallbacks + " fallbacks"));
      rows.appendChild(tr);
      (c.problems || []).forEach(function (x) { problems.appendChild(el("li", when(x.time) + ", " + (names[c.id] || c.id) + ": " + x.text)); });
    });
    if (rows.children.length) { card.querySelector(".pluto-table").hidden = false; card.querySelector(".pluto-idle").hidden = true; }
    if (problems.children.length) { problems.hidden = false; card.querySelector(".pluto-problems-title").hidden = false; }
  });
})();
</script>
"""


def _footer(s: dict, root: str = "") -> str:
    return (
        f'<a href="{PROJECT_URL}">kodi-iptv-assistant</a> {escape(s["version"])}. The same information for scripts: '
        f'<a href="{root}status.json">status.json</a>.'
    )


def _help_card() -> str:
    links = "".join(f'<li><a href="{escape(url)}">{escape(name)}</a><span>{escape(what)}</span></li>' for url, name, what in HELP)
    again = ('<li><a href="index.html#getting-started" class="show-steps" hidden>Getting started</a>'
             "<span>the setup checklist, if you hid it</span></li>")
    return f'<section class="card"><h2>Help</h2><ul class="links">{links}{again}</ul></section>'


def render_html(s: dict) -> str:
    """The home page: is kia working, what it found, and the playlists."""
    ch = s["channels"]
    tv = ch["tv"] or 0
    guided = ch["with_guide"]
    share = f" ({100 * guided / (tv + ch['radio']):.0f}%)" if isinstance(guided, int) and tv + ch["radio"] else ""
    tiles = (
        '<section class="tiles">'
        f'<div class="tile"><div class="n">{_n(tv)}</div><div class="l">TV channels</div></div>'
        f'<div class="tile"><div class="n">{_n(ch["radio"])}</div><div class="l">Radio stations</div></div>'
        f'<div class="tile"><div class="n">{_n(guided)}</div><div class="l">Channels with guide data{share}</div></div>'
        "</section>"
    )
    alerts = ""
    if s["alerts"]:
        items = "".join(f"<li>{escape(_when(a['time']))}: {escape(a['title'])}</li>" for a in s["alerts"])
        alerts = f'<section class="card"><h2>Alerts from the last refresh</h2><ul class="alerts">{items}</ul></section>'
    more = (
        '<section class="card"><h2>Details</h2><ul class="links">'
        '<li><a href="details.html">Guide coverage, streams, and reports</a>'
        "<span>how much of the guide is filled in, which playlists' streams work, and the full reports</span></li>"
        "</ul></section>"
    )
    body = (
        f'<main>{_banner(s)}{_pluto_warning(s)}{_checklist_card(s)}{tiles}{alerts}{_playlists_card(s)}'
        f'<div class="pair">{more}{_help_card()}</div></main>'
    )
    script = COPY_SCRIPT + editor.SCRIPT + PLUTO_SCRIPT + CHECKLIST_SCRIPT
    return page("kia status", body, _footer(s), script, editor.STYLE + CHECKLIST_STYLE)


def render_details(s: dict) -> str:
    """Guide coverage, streams by playlist, and the reports."""
    cov = s.get("coverage") or {}
    guide = ""
    if cov:
        guide = (
            '<section class="card"><h2>Program guide for TV channels</h2>'
            + _bar([(cov["full"], "--bar-full"), (cov["partial"], "--bar-part"), (cov["none"], "--bar-none")])
            + '<div class="legend">'
            f'<span><i style="background:var(--bar-full)"></i>Full guide: {_n(cov["full"])}</span>'
            f'<span><i style="background:var(--bar-part)"></i>Partial: {_n(cov["partial"])}</span>'
            f'<span><i style="background:var(--bar-none)"></i>Channel name only: {_n(cov["none"])}</span>'
            "</div></section>"
        )
    sources = ""
    if s.get("sources"):
        rows = "".join(
            f'<div class="source"><strong>{escape(src["name"])}</strong>'
            + _bar([(src["ok"], "--bar-full"), (src["flaky"], "--bar-part"), (src["dead"], "--bar-dead")])
            + f'<span class="counts">{_n(src["ok"])} working, {_n(src["flaky"])} flaky, {_n(src["dead"])} dead</span></div>'
            for src in s["sources"]
        )
        sources = f'<section class="card"><h2>Streams by playlist</h2><div class="sources">{rows}</div></section>'
    choose = (
        f'<section class="card" id="sources-editor"{_secure_attr(s)} hidden><h2>Channel sources</h2>'
        '<div class="body"></div></section>'
    )

    def file_item(name: str, what: str) -> str:
        twin = markdown_twin(name)
        markdown = f' (<a href="{escape(twin)}">Markdown</a>)' if twin in s["files"] else ""
        return f'<li><a href="{escape(name)}">{escape(name)}</a>{markdown}<span>{escape(what)}</span></li>'

    files = "".join(file_item(name, what) for name, what in FILES if name in s["files"])
    reports = f'<section class="card"><h2>Files and reports</h2><ul class="links">{files}</ul></section>'
    editing = (
        f'<section class="card" id="editing"{_secure_attr(s)}><h2>Editing</h2>'
        '<p class="muted">Loading…</p></section>'
    )
    body = (
        f'<main><h1>Details</h1>{guide}{sources}{choose}<div class="pair">{_secure_card(s)}{editing}</div>'
        f'<div class="pair">{reports}{_help_card()}</div>{_pluto_card(s)}{_settings_card(s)}</main>'
    )
    script = editor.EDITING_SCRIPT + sourceeditor.SCRIPT + PLUTO_SCRIPT
    return page("kia details", body, _footer(s), script, editor.STYLE + sourceeditor.STYLE)


def render_playlist(s: dict, pl: dict, entries: list) -> str:
    """One playlist's page: its addresses, its channels, and (for a profile) Edit."""
    root = "../"
    # kia's own TV-only playlist has no settings to edit.
    tag = f' data-profile="{escape(pl["name"])}"' if pl["name"] and not pl.get("builtin") else ""
    if pl.get("builtin"):
        tag = " data-builtin"

    def row(e) -> str:
        return (
            f'<tr data-id="{escape(e.tvg_id or "")}" data-key="{escape(e.key)}"><td class="num">{escape(e.attrs.get("tvg-chno", ""))}</td>'
            f'<td class="name">{escape(e.name)}</td><td class="now" hidden></td><td class="next" hidden></td>'
            f'<td>{escape(e.group.replace(";", ", "))}</td></tr>\n'
        )

    # TV, then radio, each under its own heading (Kodi shows them apart too).
    tv = [e for e in entries if not e.is_radio]
    radio = [e for e in entries if e.is_radio]
    both = bool(tv and radio)
    bodies = "".join(
        f'<tbody class="group-{kind}">'
        + (f'<tr class="group-head"><th colspan="9">{title} ({len(group):,})</th></tr>\n' if both else "")
        + "".join(row(e) for e in group) + "</tbody>"
        for kind, title, group in (("tv", "TV channels", tv), ("radio", "Radio stations", radio))
        if group
    )
    if not entries:
        bodies = ('<tbody><tr><td colspan="9" class="muted">No channels match this playlist\'s settings'
                  + (", so it's empty. Edit this playlist to pick other categories or names." if pl["name"] and not pl.get("builtin")
                     else " yet. They appear after kia's first refresh.") + "</td></tr></tbody>")
    filter_box = (
        f'<input type="search" placeholder="Filter these {len(entries):,} channels" data-filter="channels">'
        if len(entries) > FILTER_ROWS
        else ""
    )
    guide_note = (
        "Its own guide, with only these channels."
        if pl["guide"] != "guide.xml.gz"
        else "The program guide."
    )
    body = (
        f'<main><section class="card" id="playlists" data-root="{root}"{_secure_attr(s)}>'
        f'<div class="playlist"{tag}><h1>{escape(pl["title"])}</h1><p class="muted">{_counts(pl)}</p></div>'
        f"{_profiles_error(s) if pl['name'] else ''}"
        "<h2>Playlist address</h2>"
        "<p>Use this as the <strong>M3U playlist URL</strong> in IPTV Simple Client or your player.</p>"
        f'{_address("playlist", pl["url"], pl["file"], root, "playlist address")}'
        "<h2>Guide address</h2>"
        f"<p>{guide_note} Kodi finds it through the playlist; some other players ask for it separately.</p>"
        f'{_address("guide", pl["guide_url"], pl["guide"], root, "guide address")}'
        "</section>"
        f'<section class="card" id="whats-on" data-root="{root}"><h2>Channels</h2>{filter_box}'
        '<div class="view" hidden><button type="button" data-view="list" aria-pressed="true">On now</button>'
        '<button type="button" data-view="timeline" aria-pressed="false">Next 6 hours</button></div>'
        '<p class="guide-age bad-note" hidden></p>'
        '<div class="wide"><table id="channels">'
        '<thead><tr><th class="num">No.</th><th class="name">Channel</th><th class="now" hidden>On now</th>'
        '<th class="next" hidden>Next</th><th>Category</th></tr></thead>'
        f"{bodies}</table></div></section></main>"
    )
    script = COPY_SCRIPT + FILTER_SCRIPT + editor.SCRIPT + guideview.SCRIPT + channeleditor.SCRIPT
    style = editor.STYLE + guideview.STYLE + channeleditor.STYLE
    return page(f"{pl['title']} - kia", body, _footer(s, root), script, style, root=root)


def write_pages(output_dir: Path, s: dict) -> None:
    """index.html, details.html, and a page per playlist in playlists/."""
    _write(output_dir / "index.html", render_html(s))
    _write(output_dir / "details.html", render_details(s))
    folder = output_dir / "playlists"
    keep = set()
    for pl in _playlists(s):
        path = output_dir / pl["file"]
        entries = parse(path.read_text()) if path.exists() else []
        name = Path(pl["page"]).name
        _write(folder / name, render_playlist(s, pl, entries))
        keep.add(name)
    for old in folder.glob("*.html"):
        if old.name not in keep:
            old.unlink()
