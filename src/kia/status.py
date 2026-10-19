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
from kia.m3u import parse

# Files in output/ the page links to, with what each one is for.
FILES = [
    ("curated.m3u", "Playlist for Kodi (IPTV Simple Client's M3U playlist URL)"),
    ("guide.xml.gz", "Program guide (found through the playlist; no Kodi setting needed)"),
    ("report.md", "Stream check: working, flaky, and dead streams"),
    ("guide-report.md", "Guide coverage by channel and site"),
    ("mapping-report.md", "Which guide sites were matched to which channels"),
]
RESULTS = {
    "running": "Refreshing now",
    "ok": "Last refresh worked",
    "alerts": "Last refresh worked, with alerts",
    "failed": "Last refresh failed",
    "never": "Not run yet",
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
        "channels": {
            "tv": len(entries) - radio,
            "radio": radio,
            "with_guide": counts.get("guide_channels"),
        },
        "alerts": recent_alerts(state_dir / "alerts.log", run.get("started")),
        "playlist_url": f"{public_url}/curated.m3u" if public_url else None,
        "files": [name for name, _ in FILES if (output_dir / name).exists()],
    }


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
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return iso
    return f"{dt:%a %b} {dt.day}, {dt:%H:%M %Z}"


def render_html(s: dict) -> str:
    run, ch = s["last_run"], s["channels"]
    rows = [
        ("Last refresh", f"{_when(run['started'])}" + (f", took {run['minutes']} min" if run.get("minutes") is not None else "")),
        ("Next refresh", _when(s["next_run"])),
        ("TV channels", f"{ch['tv']}"),
        ("Radio stations", f"{ch['radio']}"),
        ("Channels with guide data", "-" if ch["with_guide"] is None else f"{ch['with_guide']}"),
    ]
    result = RESULTS.get(s["result"], s["result"])
    table = f'<tr class="result-{escape(s["result"])}"><th>Status</th><td>{escape(result)}</td></tr>\n'
    table += "\n".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in rows)
    alerts = "".join(f"<li>{escape(_when(a['time']))}: {escape(a['title'])}</li>" for a in s["alerts"])
    alerts_html = f"<h2>Alerts from the last refresh</h2><ul>{alerts}</ul>" if alerts else ""
    files = "".join(
        f'<li><a href="{escape(name)}">{escape(name)}</a> <span>{escape(what)}</span></li>'
        for name, what in FILES
        if name in s["files"]
    )
    playlist = (
        f'<p>Playlist URL for Kodi: <code>{escape(s["playlist_url"])}</code></p>' if s["playlist_url"] else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kia status</title>
<style>
:root {{ --bg: #fafafa; --fg: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --bad: #b3261e; --good: #1b7f3b; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #161617; --fg: #f5f5f7; --muted: #a1a1a6; --line: #3a3a3c; --bad: #ff8a80; --good: #7ee2a0; }}
}}
body {{ background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 44rem; padding: 1.5rem 1rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 1rem; }}
h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border-bottom: 1px solid var(--line); padding: .45rem .25rem; text-align: left; vertical-align: top; }}
th {{ color: var(--muted); font-weight: normal; width: 45%; }}
.result-failed td, .result-alerts td {{ color: var(--bad); }} .result-ok td {{ color: var(--good); }}
ul {{ padding-left: 1.2rem; }} li span {{ color: var(--muted); }}
code {{ word-break: break-all; }}
a {{ color: inherit; }}
footer {{ color: var(--muted); font-size: .85rem; margin-top: 2rem; }}
</style>
</head>
<body>
<h1>kia status</h1>
<table>
{table}
</table>
{alerts_html}
<h2>Files</h2>
{playlist}
<ul>{files}</ul>
<footer>kodi-iptv-assistant {escape(s['version'])}. The same information for scripts: <a href="status.json">status.json</a>.</footer>
</body>
</html>
"""
