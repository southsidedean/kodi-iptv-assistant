"""A status page (index.html) and status.json in the output folder.

They answer "is kia working?" from any browser on the network: when it last ran, whether
that run worked, what it found, and any alerts it raised. kia rewrites both whenever a
run starts or ends and whenever the playlist or guide changes.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path

from kia import __version__
from kia.htmlpage import page
from kia.history import readable
from kia.m3u import parse

# Files in output/ the page links to, with what each one is for.
FILES = [
    ("curated.m3u", "Playlist for Kodi (IPTV Simple Client's M3U playlist URL)"),
    ("guide.xml.gz", "Program guide (found through the playlist; no Kodi setting needed)"),
    ("report.html", "Stream check: working, flaky, and dead streams"),
    ("guide-report.html", "Guide coverage by channel and site"),
    ("mapping-report.html", "Which guide sites match which channels, including matches by name to check"),
]
PROJECT_URL = "https://github.com/southsidedean/kodi-iptv-assistant"
DOCS_URL = f"{PROJECT_URL}/blob/main/docs"
# Docs pages for someone looking at this page because something seems wrong.
HELP = [
    (f"{DOCS_URL}/troubleshooting.md", "Troubleshooting", "no channels, no guide, failed refreshes, alerts"),
    (f"{DOCS_URL}/kodi.md", "Setting up Kodi", "the playlist, repeated refresh, and channel numbers"),
    (f"{DOCS_URL}/configuration.md", "Settings", "playlists, refresh time, and when changes take effect"),
    (f"{DOCS_URL}/how-it-works.md", "How it works", "what each nightly step does, and the reports"),
    (f"{DOCS_URL}/alerts.md", "Alerts", "what each alert means, and getting them on your phone"),
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
    saved_path = state_dir / "run.json"
    saved = _load(saved_path)
    saved.update(run)
    _write(saved_path, json.dumps(saved, indent=1, sort_keys=True))
    status = collect(output_dir, state_dir, saved, public_url)
    _write(output_dir / "status.json", json.dumps(status, indent=1))
    _write(output_dir / "index.html", render_html(status))
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
        "files": [n for name, _ in FILES for n in (name, markdown_twin(name)) if n and (output_dir / n).exists()],
    }


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


def _playlists_card(s: dict) -> str:
    """Everything, plus one playlist per profile, each with its address and a Copy button."""
    ch = s["channels"]
    rows = [("Everything", "curated.m3u", ch["tv"] or 0, ch["radio"] or 0, s["playlist_url"])]
    rows += [(p["title"], p["file"], p["tv"], p["radio"], p.get("url")) for p in s.get("profiles", [])]
    items = []
    for i, (title, file, tv, radio, url) in enumerate(rows):
        counts = f"{_n(tv)} TV channels" + (f", {_n(radio)} radio stations" if radio else "")
        address = (
            f'<div class="copy"><code id="playlist-{i}">{escape(url)}</code>'
            f'<button type="button" data-copy="playlist-{i}">Copy</button></div>'
            if url
            else f'<a href="{escape(file)}">{escape(file)}</a>'
        )
        items.append(f'<div class="playlist"><div><strong>{escape(title)}</strong> <span class="muted">{counts}</span></div>{address}</div>')
    error = (
        f'<p class="bad-note">The profiles file has a mistake, so the profile playlists weren\'t updated: {escape(s["profiles_error"])}</p>'
        if s.get("profiles_error")
        else ""
    )
    return (
        '<section class="card"><h2>Playlists</h2>'
        "<p>Use one of these as the <strong>M3U playlist URL</strong> in IPTV Simple Client or your player.</p>"
        f'{error}<div class="playlists">{"".join(items)}</div></section>'
    )


def render_html(s: dict) -> str:
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

    alerts = ""
    if s["alerts"]:
        items = "".join(f"<li>{escape(_when(a['time']))}: {escape(a['title'])}</li>" for a in s["alerts"])
        alerts = f'<section class="card"><h2>Alerts from the last refresh</h2><ul class="alerts">{items}</ul></section>'

    kodi = _playlists_card(s)

    def file_item(name: str, what: str) -> str:
        twin = markdown_twin(name)
        markdown = f' (<a href="{escape(twin)}">Markdown</a>)' if twin in s["files"] else ""
        return f'<li><a href="{escape(name)}">{escape(name)}</a>{markdown}<span>{escape(what)}</span></li>'

    files = "".join(file_item(name, what) for name, what in FILES if name in s["files"])
    help_links = "".join(
        f'<li><a href="{escape(url)}">{escape(name)}</a><span>{escape(what)}</span></li>' for url, name, what in HELP
    )
    pair = (
        '<div class="pair">'
        f'<section class="card"><h2>Files and reports</h2><ul class="links">{files}</ul></section>'
        f'<section class="card"><h2>Help</h2><ul class="links">{help_links}</ul></section>'
        "</div>"
    )
    body = f"<main>{_banner(s)}{tiles}{guide}{sources}{alerts}{kodi}{pair}</main>"
    footer = (
        f'<a href="{PROJECT_URL}">kodi-iptv-assistant</a> {escape(s["version"])}. The same information for scripts: '
        '<a href="status.json">status.json</a>.'
    )
    return page("kia status", body, footer, COPY_SCRIPT)
