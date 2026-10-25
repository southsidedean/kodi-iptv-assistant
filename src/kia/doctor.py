"""kia doctor: plain answers to "why?", from what kia has already written down.

Every refresh leaves a record: the Dashboard's status.json, the stream report, each
stream's history, the guide match, each guide site's log, the stack's tiles. The answers
to most questions ("why does this channel show only its name?", "why did last night's
refresh fail?") are in those files; doctor reads them and says what they mean, with the
file each answer comes from and what to do about it. It changes nothing, needs no network,
and gives the same answer every time for the same files, so it can be tested.

    kia doctor                  how kia is doing, and anything that needs you
    kia doctor channel <name>   one channel: its stream, and its guide
    kia doctor plex             why Plex shows no channels, or too few
    kia doctor ... --json       the same, for scripts (and, later, kia's MCP server)

Everything it reads was written by kia, but parts of it come from strangers (channel
names, guide sites' errors), so it's shown as text and never run or followed.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import channels, m3u
from kia.config import Config
from kia.history import readable

# Levels, worst first. A problem needs you; a note is worth knowing; ok is reassurance.
PROBLEM, NOTE, OK = "problem", "note", "ok"
# A nightly run more than this long ago means kia stopped refreshing (a day, plus slack
# for a slow run).
STALE_AFTER = timedelta(hours=26)
# The most channels doctor describes for one search; more means the search is too loose.
MAX_MATCHES = 5
# The last few error lines of a guide site's log are enough to see why it failed.
LOG_LINES = 3
DOCS = "docs/troubleshooting.md"
# Logins in addresses, as an ffprobe error or a guide site's log can quote them:
# - before the host, "https://me:secret@host/" (up to the last @ before the path, so a
#   password with an @ in it is hidden whole), also "//me:secret@host" and "me:secret@host:80";
# - in the query, "get.php?username=me&password=secret", the usual IPTV panel form;
# - in the path, "/live/me/secret/123.ts", the same panels' stream addresses;
# - any of these percent-encoded inside another address ("%3A%2F%2Fme%3Asecret%40host").
_LOGIN_RES = (
    (re.compile(r"(?i)((?:[a-z][a-z0-9+.-]*:)?//)[^/\s?#]*@"), r"\1•••@"),
    (re.compile(r"(?i)([?&;](?:user(?:name)?|login|pass(?:word)?|pwd|token|key|auth)=)[^&\s#]*"), r"\1•••"),
    (re.compile(r"(?i)(/(?:live|movie|series|timeshift)/)[^/\s?#]+/[^/\s?#]+/"), r"\1•••/•••/"),
    (re.compile(r"(?i)(%3A%2F%2F)[^/\s?#]*?%40"), r"\1•••%40"),
    # Without a scheme, "me:secret@host:8080/...": a name, a colon, no space, then @ and a
    # host (an email address has no colon before its @, so it's left alone).
    (re.compile(r"(?<![\w.%+:/-])[\w.%+-]+:[^\s@/]+@(?=[\w.-]+(?::\d+)?(?:[/?#\s]|$))"), "•••@"),
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def hide_logins(text: str) -> str:
    """text with the user names and passwords in its addresses hidden ("https://•••@host/...";
    see _LOGIN_RES for the forms)."""
    for pattern, replacement in _LOGIN_RES:
        text = pattern.sub(replacement, text)
    return text


def _quote(text: object, limit: int = 200) -> str:
    """Third-party text (a stream's error, a guide site's log line) made safe to show: on
    one line, cut to limit, and with any login in an address hidden, as the Dashboard
    hides them in playlist addresses."""
    text = _CONTROL_RE.sub(" ", " ".join(str(text).split()))
    return hide_logins(text)[:limit]


@dataclass
class Finding:
    level: str
    title: str
    detail: str = ""
    evidence: list[str] = field(default_factory=list)  # which files say so
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Report:
    subject: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def worst(self) -> str:
        levels = {f.level for f in self.findings}
        return PROBLEM if PROBLEM in levels else NOTE if NOTE in levels else OK

    def to_dict(self) -> dict:
        return {"subject": self.subject, "worst": self.worst, "findings": [f.to_dict() for f in self.findings]}


def _json(path: Path) -> dict | None:
    """A JSON object kia wrote, or None if it isn't there, can't be read, or is damaged into
    something else (a list, say): every file doctor reads holds an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_of(value: object, kind: type) -> list:
    """value if it's a list, keeping only the items of kind; [] for anything else."""
    return [v for v in value if isinstance(v, kind)] if isinstance(value, list) else []


def _when(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


# How kia is doing


def overview(cfg: Config, now: datetime | None = None) -> Report:
    """The state of the last refresh, its alerts, the playlists, the guide, and the stack."""
    now = now or datetime.now(timezone.utc)
    out = cfg.output_dir
    report = Report("kia")
    status = _json(out / "status.json")
    if not isinstance(status, dict):
        report.findings.append(Finding(PROBLEM, "kia hasn't written its status yet",
                                       "There's no output/status.json, so kia hasn't started, or its first refresh is still running.",
                                       ["output/status.json"], "Wait for the first refresh (it can take up to an hour), then ask again."))  # fmt: skip
        return report
    report.findings += _refresh(status, now)
    report.findings += [Finding(NOTE, f"Alert: {a.get('title', '?')}", f"From the last refresh ({readable(a.get('time'))}).",
                                ["state/alerts.log"], f"{DOCS}#alerts") for a in list_of(status.get("alerts"), dict)]  # fmt: skip
    report.findings += _sources(status)
    report.findings += _coverage(status)
    report.findings += _stack(_json(out / "stack.json"))
    if status.get("env_private") is False:
        report.findings.append(Finding(PROBLEM, ".env can be read by other users on this computer",
                                       "It holds kia's passwords and tokens.", ["state/settings.json"],
                                       "In the kia folder: chmod 600 .env"))  # fmt: skip
    pluto = _json(out / "pluto.json")
    if isinstance(pluto, dict) and pluto.get("fallback"):
        report.findings.append(Finding(NOTE, "A Pluto TV channel is playing without kia's fix",
                                       "Pluto wouldn't open a session, so kia is passing on its plain link, which can freeze at ad breaks. kia tries again every 5 minutes.",
                                       ["output/pluto.json"], "docs/pluto-under-the-hood.md#when-pluto-wont-play-along"))  # fmt: skip
    # Plex's problems too, when kia feeds a Plex (its notes and all-clear stay in `kia doctor plex`).
    report.findings += [f for f in plex(cfg).findings if f.level == PROBLEM]
    if not report.findings or all(f.level == OK for f in report.findings):
        report.findings.append(Finding(OK, "Nothing needs you", "No alerts, no playlist whose streams all fail, and everything in the stack answers."))
    return report


def _refresh(status: dict, now: datetime) -> list[Finding]:
    result = status.get("result")
    last = status.get("last_run") or {}
    finished = _when(last.get("finished"))
    if result == "never":
        return [Finding(NOTE, "The first refresh hasn't finished", "It can take up to an hour.", ["output/status.json"])]
    if result == "running":
        return [Finding(OK, "A refresh is running now", f"Started {readable(last.get('started'))}.", ["output/status.json"])]
    found = []
    if result == "failed":
        found.append(Finding(PROBLEM, "The last refresh failed",
                             f"It finished {readable(last.get('finished'))} with exit code {last.get('exit_code')}. The playlist or guide may not have been updated.",
                             ["output/status.json"], f"{DOCS}#the-dashboard-says-the-refresh-failed"))  # fmt: skip
    elif result in ("ok", "alerts"):
        found.append(Finding(OK, "The last refresh worked", f"Finished {readable(last.get('finished'))}, in {last.get('minutes')} minutes.",
                             ["output/status.json"]))  # fmt: skip
    if finished and now - finished > STALE_AFTER:
        found.append(Finding(PROBLEM, "kia hasn't refreshed for over a day",
                             f"The last refresh finished {readable(last.get('finished'))}. kia may have stopped, or its computer was off.",
                             ["output/status.json"], "Check that kia is running: docker compose ps, in the kia folder."))  # fmt: skip
    return found


def _sources(status: dict) -> list[Finding]:
    found = []
    for s in list_of(status.get("sources"), dict):
        ok, flaky, dead = (s.get(k) or 0 for k in ("ok", "flaky", "dead"))
        if ok + flaky == 0 and dead:
            found.append(Finding(PROBLEM, f"None of the streams in {s.get('name')} work",
                                 f"All {dead} failed in the last check. The playlist may be out of date, or its server may be turning kia away.",
                                 ["output/report.json"], f"{DOCS}#a-channel-wont-play"))  # fmt: skip
    return found


def _coverage(status: dict) -> list[Finding]:
    c = status.get("coverage") if isinstance(status.get("coverage"), dict) else {}
    total = sum(c.get(k) or 0 for k in ("full", "partial", "none"))
    if total and (c.get("none") or 0) / total > 0.5:
        return [Finding(NOTE, "Most TV channels have no guide",
                        f"{c.get('none')} of {total} show only their name. That's common for some playlists; mapping-report.html says which channels matched nothing.",
                        ["output/status.json", "output/mapping-report.html"], f"{DOCS}#channels-but-the-guide-is-empty")]  # fmt: skip
    return []


def _stack(stack: object) -> list[Finding]:
    if not isinstance(stack, dict):
        return []
    return [Finding(PROBLEM, f"{s.get('title') or s.get('service')} isn't answering",
                    f"kia knocked on its port at {readable(stack.get('checked'))} and got no answer. It may be starting, or it stopped.",
                    ["output/stack.json"], "In the kia folder: docker compose ps, then docker compose logs " + str(s.get("service")))
            for s in list_of(stack.get("services"), dict) if s.get("ok") is False]  # fmt: skip


# One channel


def channel(cfg: Config, query: str) -> list[Report]:
    """What kia knows about the channels matching query: an exact ID, or part of a name."""
    out = cfg.output_dir
    playlist = out / "curated.m3u"
    kept = m3u.parse(playlist.read_text(encoding="utf-8")) if playlist.exists() else []
    streams = list_of((_json(out / "report.json") or {}).get("streams"), dict)
    removed = channels.load(cfg.state_dir)["removed"]
    q = query.strip().lower()
    if not q:
        return [Report(query, [Finding(PROBLEM, "Say which channel", "Give part of its name, or its ID (tvg-id).")])]

    def matches(name: str, tvg_id: str) -> bool:
        return (tvg_id or "").lower() == q or q in (name or "").lower()

    found = [e for e in kept if matches(e.name, e.tvg_id)]
    if not found:
        # Not in the playlists: tonight's check may have dropped it, or you removed it.
        gone = {}
        for s in streams:
            if matches(s.get("name", ""), s.get("tvg_id", "")):
                gone.setdefault(s.get("tvg_id") or s.get("name"), []).append(s)
        gone_removed = {k: v for k, v in removed.items() if matches(v.get("name", ""), "") or k.lower() == q}
        if not gone and not gone_removed:
            return [Report(query, [Finding(PROBLEM, "No channel by that name",
                                           "It isn't in the playlists, and no playlist kia checks has it. Check the spelling, or that its playlist is on in Sources.",
                                           ["output/curated.m3u", "output/report.json"], "docs/configuration.md#choosing-channel-sources-on-the-dashboard")])]  # fmt: skip
        reports = [_removed(k, v) for k, v in list(gone_removed.items())[:MAX_MATCHES]]
        reports += [_dropped(name, rows, cfg) for name, rows in list(gone.items())[:MAX_MATCHES] if name not in gone_removed]
        return reports[:MAX_MATCHES]
    if len(found) > MAX_MATCHES:
        names = ", ".join(e.name for e in found[: MAX_MATCHES * 2])
        return [Report(query, [Finding(NOTE, f"{len(found)} channels match", f"Say which one, by more of its name or its ID: {names}...")])]
    return [_kept(e, streams, cfg) for e in found]


def _histories(cfg: Config) -> dict:
    """Each stream's record of past checks. Read directly: kia.history.History would rename a
    damaged file, and doctor changes nothing."""
    records = _json(cfg.state_dir / "history.json")
    return records if isinstance(records, dict) else {}


def _stream_rows(e: m3u.Entry, streams: list[dict]) -> list[dict]:
    return [s for s in streams if (e.tvg_id and s.get("tvg_id") == e.tvg_id) or (not e.tvg_id and s.get("name") == e.name)]


def _history_line(rec: object) -> str:
    if not isinstance(rec, dict) or not rec:
        return ""
    line = f" It has worked {rec.get('successes', 0)} of {rec.get('checks', 0)} checks since {readable(rec.get('first_seen'))}"
    if rec.get("consecutive_failures"):
        line += f", and failed the last {rec['consecutive_failures']} in a row"
    return line + "."


def _kept(e: m3u.Entry, streams: list[dict], cfg: Config) -> Report:
    report = Report(f"{e.name} ({e.tvg_id or 'no ID'})")
    rows = _stream_rows(e, streams)
    chosen = next((s for s in rows if s.get("selected")), rows[0] if rows else None)
    history = _histories(cfg).get(chosen["url"], {}) if chosen else {}
    if chosen and chosen.get("status") == "flaky":
        report.findings.append(Finding(NOTE, "Its stream failed in the last check, and is kept for now",
                                       f"It worked before, so it stays until it fails more nights in a row (grace_failures).{_history_line(history)}",
                                       ["output/report.json", "state/history.json"], f"{DOCS}#a-channel-wont-play"))  # fmt: skip
    elif chosen:
        height = f", {chosen['height']}p" if chosen.get("height") else ""
        report.findings.append(Finding(OK, "Its stream works", f"It opened in the last check{height}.{_history_line(history)}",
                                       ["output/report.json", "state/history.json"]))  # fmt: skip
    if len(rows) > 1:
        report.findings.append(Finding(NOTE, f"It's in {len(rows)} playlists", "kia plays whichever copy tested best: " +
                                       ", ".join(f"{s.get('source')} ({s.get('status')})" for s in rows) + ".", ["output/report.json"]))  # fmt: skip
    report.findings += _guide(e, cfg)
    return report


def _dropped(name: str, rows: list[dict], cfg: Config) -> Report:
    history = _histories(cfg)
    report = Report(f"{rows[0].get('name')} ({rows[0].get('tvg_id') or 'no ID'})")
    errors = "; ".join(f"{s.get('source')}: {_quote(s.get('error') or s.get('status'))}" for s in rows)
    records = [r for s in rows if isinstance(r := history.get(s.get("url", "")), dict)]
    best = max(records, key=lambda r: r.get("successes") if isinstance(r.get("successes"), int) else 0, default={})
    report.findings.append(Finding(PROBLEM, "It isn't in the playlists: its stream failed",
                                   f"The last check couldn't play it ({errors}).{_history_line(best)}",
                                   ["output/report.json", "state/history.json"], f"{DOCS}#a-channel-wont-play"))  # fmt: skip
    return report


def _removed(key: str, info: dict) -> Report:
    good = info.get("good_nights", 0)
    return Report(f"{info.get('name', key)} ({key})", [Finding(NOTE, "You took it out of the playlists",
                  f"On the Dashboard, {readable(info.get('when'))}. It comes back by itself after its stream works "
                  f"{channels.BACK_AFTER_NIGHTS} nights in a row ({good} so far), or now with Put back on the Everything list, on the secure page.",
                  ["state/channels.json"], "docs/how-it-works.md#the-dashboard")])  # fmt: skip


# A channel's guide


def _guide(e: m3u.Entry, cfg: Config) -> list[Finding]:
    if e.is_radio:
        return [Finding(OK, "No guide is expected", "It's a radio station, and almost none have guide data.")]
    if not e.tvg_id:
        return [Finding(PROBLEM, "It has no ID, so it can't have a guide",
                        "Players connect a channel to its guide by its tvg-id, and its playlist gives it none.",
                        ["output/curated.m3u"], "docs/guide-under-the-hood.md#matching-kia-map")]  # fmt: skip
    work = cfg.epg.work_dir
    if cfg.upstream_url:
        return _choice(e, _json(work / "guide-choices.json"), [], ["work/guide-choices.json"], cfg)
    candidates = list_of((_json(work / "mapping.json") or {}).get(e.tvg_id), str)
    if not candidates:
        return [Finding(PROBLEM, "No guide source matched it",
                        "No guide site lists its ID, and no site channel has exactly its name in the same country, so it shows only its name.",
                        ["work/mapping.json", "output/mapping-report.html"],
                        "Look for it under possible matches in mapping-report.html; a match you've checked goes in accept_name_matches (docs/guide-under-the-hood.md#matching-kia-map).")]  # fmt: skip
    return _choice(e, _json(work / "guide-choices.json"), candidates, ["work/mapping.json", "work/guide-choices.json"], cfg)


def _choice(e: m3u.Entry, choices: object, candidates: list[str], evidence: list[str], cfg: Config) -> list[Finding]:
    if not isinstance(choices, dict):
        return [Finding(NOTE, "It has guide sources; which one it uses isn't recorded yet",
                        f"Matched to: {', '.join(candidates) or 'the upstream server'}. The next guide build records which one it picks.",
                        evidence)]  # fmt: skip
    c = choices.get(e.tvg_id) if isinstance(choices.get(e.tvg_id), dict) else {}
    site = c.get("site")
    if site:
        hours = c.get("hours_24h") or 0
        level, title = (OK, "It has a full guide") if hours >= 20 else (NOTE, "It has a partial guide")
        detail = f"From {site}: {hours} of the next 24 hours, {c.get('programs')} programs."
        if c.get("ends"):
            detail += f" Its listings run to {readable(c['ends'])}, and the quick refreshes add more as the day goes on."
        return [Finding(level, title, detail, evidence)]
    # Matched, but none of its sources had anything for the next 24 hours.
    found = [Finding(PROBLEM, "Its guide sources sent nothing for the next day",
                     f"Matched to {', '.join(candidates) or 'the upstream server'}, but none of them had listings for it, so it shows only its name.",
                     evidence, "docs/guide-under-the-hood.md#when-a-channels-guide-is-wrong-or-missing")]  # fmt: skip
    streaks = (_json(cfg.state_dir / "alert-state.json") or {}).get("site_failures")
    streaks = streaks if isinstance(streaks, dict) else {}
    for site in candidates:
        lines = _log_errors(cfg.epg.work_dir / "logs" / f"{site}.log")
        if streaks.get(site) or lines:
            failed = f"It has failed {streaks[site]} refreshes in a row. " if streaks.get(site) else ""
            found.append(Finding(NOTE, f"{site} had errors", failed + ("Its log says: " + " / ".join(lines) if lines else ""),
                                 [f"work/logs/{site}.log", "state/alert-state.json"]))  # fmt: skip
    return found


def _log_errors(path: Path) -> list[str]:
    """The last few error lines of a guide site's log (third-party text: shown, never run)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    errors = [_quote(line) for line in lines if "ERR" in line or "Error" in line or "error:" in line]
    return errors[-LOG_LINES:]


# Plex


PLEX_MAX = 480  # channels Plex takes from one tuner
THREADFINS = (("threadfin", "Threadfin"), ("threadfin-2", "Threadfin 2"))
STALE_TUNERS = timedelta(days=2)  # plex-tuners runs after every refresh, so older means it stopped


def plex(cfg: Config, env: dict | None = None, now: datetime | None = None) -> Report:
    """Why Plex shows no channels (or too few): Threadfin, its playlist, what the last
    `kia plex-tuners` found in Plex (state/plex-tuners.json), and Plex's codecs. Offline, like
    the rest: plex-tuners is the step that talks to Plex, after each refresh."""
    import os

    from kia import m3u, plex as plex_module

    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    base = cfg.config_file.parent if cfg.config_file else Path(".")
    report = Report("Plex")
    url = env.get("KIA_PLEX_URL", "")
    folders = [(f, title) for f, title in THREADFINS if (base / f / "conf" / "settings.json").exists()]
    if not url and not folders:
        report.findings.append(Finding(NOTE, "Plex isn't set up with kia",
                                       "There's no Threadfin and no KIA_PLEX_URL, so kia isn't feeding a Plex.",
                                       [".env", "threadfin/"], "docs/plex.md"))  # fmt: skip
        return report
    stack = _json(cfg.output_dir / "stack.json") or {}
    answering = {x.get("service"): x.get("ok") for x in list_of(stack.get("services"), dict)}
    for folder, title in folders:
        report.findings += _threadfin(base, folder, title, answering.get(folder), cfg, m3u)
    if not folders:
        report.findings.append(Finding(PROBLEM, "There's no Threadfin",
                                       "Plex reaches kia's channels through Threadfin, a tuner kia sets up, and none is set up here.",
                                       ["threadfin/"], "docs/plex.md#2-start-threadfin-with-kia"))  # fmt: skip
    report.findings += _plex_tuners(_json(cfg.state_dir / "plex-tuners.json"), url, now)
    if url and plex_module.in_stack(url) and not plex_module.has_codecs(base):
        report.findings.append(Finding(PROBLEM, "Plex hasn't downloaded its codecs yet",
                                       "Without them every Live TV channel fails with \"Could not tune channel\". kia plays a few seconds "
                                       "of video to make Plex fetch them, once a day; playing any video in Plex does it at once.",
                                       ["plex/config/.../Codecs"], "docs/plex-under-the-hood.md#the-codec-warm-up"))  # fmt: skip
    return report


def _threadfin(base: Path, folder: str, title: str, ok: object, cfg: Config, m3u) -> list[Finding]:
    found = []
    if ok is False:
        found.append(Finding(PROBLEM, f"{title} isn't answering", "kia knocked on its port and got no answer.",
                             ["output/stack.json"], f"In the kia folder: docker compose ps, then docker compose logs {folder}"))  # fmt: skip
    settings = _json(base / folder / "conf" / "settings.json") or {}
    files = (settings.get("files") or {}).get("m3u") if isinstance(settings.get("files"), dict) else None
    sources = [f.get("file.source", "") for f in files.values() if isinstance(f, dict)] if isinstance(files, dict) else []
    names = [Path(s.rsplit("/", 1)[-1]).name for s in sources if isinstance(s, str) and s.endswith(".m3u")]
    if not names:
        found.append(Finding(PROBLEM, f"{title} reads no kia playlist", "Its settings name no playlist, so it has no channels to offer Plex.",
                             [f"{folder}/conf/settings.json"], "docs/plex.md#3-check-threadfin"))  # fmt: skip
        return found
    for name in names:
        path = cfg.output_dir / name
        if not path.exists():
            found.append(Finding(PROBLEM, f"{title} reads {name}, which doesn't exist",
                                 "Make a playlist with that name on the Dashboard (Playlists, then Make a playlist, with For Plex through Threadfin ticked).",
                                 [f"{folder}/conf/settings.json", f"output/{name}"], "docs/plex.md#1-make-a-playlist-for-plex"))  # fmt: skip
            continue
        tv = sum(1 for e in m3u.parse(path.read_text(encoding="utf-8", errors="replace")) if not e.is_radio)
        if tv > PLEX_MAX:
            found.append(Finding(PROBLEM, f"{name} has {tv} TV channels, more than Plex takes from one tuner ({PLEX_MAX})",
                                 "Plex leaves the rest out. Make the playlist smaller, or split it between two Threadfins.",
                                 [f"output/{name}"], "docs/plex.md#before-you-start"))  # fmt: skip
        elif tv == 0:
            found.append(Finding(PROBLEM, f"{name} has no TV channels", "Threadfin has nothing to offer Plex.",
                                 [f"output/{name}"], "docs/plex.md#1-make-a-playlist-for-plex"))  # fmt: skip
        else:
            found.append(Finding(OK, f"{title} reads {name}: {tv} TV channels", "Within Plex's limit for one tuner.", [f"output/{name}"]))
    return found


def _plex_tuners(record: dict | None, url: str, now: datetime) -> list[Finding]:
    """What the last `kia plex-tuners` found: the DVR in Plex, or why it couldn't add one."""
    if not url:
        return [Finding(NOTE, "kia doesn't keep Plex's channel list up to date",
                        "KIA_PLEX_URL isn't set, so kia can't add the channels a playlist gains to Plex. Set it (and KIA_PLEX_TOKEN for "
                        "a Plex outside kia's stack), or in Plex choose the tuner's channel count, Rescan, and Save.",
                        [".env"], "docs/plex.md#keeping-it-up-to-date")]  # fmt: skip
    if not record:
        return [Finding(NOTE, "kia hasn't added the tuners to Plex yet",
                        "It does after the next refresh, or now with Add Live TV to Plex on the Stack page (kia plex-tuners).",
                        ["state/plex-tuners.json"], "docs/plex.md#let-kia-add-them")]  # fmt: skip
    when = readable(record.get("when"))
    said = [_quote(x) for x in list_of(record.get("said"), str)][-3:]
    if record.get("missing"):
        return [Finding(PROBLEM, "kia can't reach Plex: settings are missing",
                        "Set these in .env, then restart kia: " + "; ".join(_quote(m) for m in list_of(record.get("missing"), str)) + ".",
                        ["state/plex-tuners.json", ".env"], "docs/plex.md#let-kia-add-them")]  # fmt: skip
    if record.get("error") or not record.get("ok"):
        return [Finding(PROBLEM, "kia couldn't add the tuners to Plex",
                        f"On {when} it said: {_quote(record.get('error') or ' / '.join(said) or 'nothing')}.",
                        ["state/plex-tuners.json"], "docs/plex-under-the-hood.md#when-something-doesnt-line-up")]  # fmt: skip
    tuners = list_of(record.get("tuners"), dict)
    count = sum(t.get("channels") or 0 for t in tuners if isinstance(t.get("channels"), int))
    found = [Finding(OK, "Plex has kia's tuners", f"On {when}, kia added {len(tuners)} tuner(s) with {count} channels to "
                     f"DVR {_quote(record.get('dvr'))} and asked Plex to load the guide.", ["state/plex-tuners.json"])]  # fmt: skip
    seen = _when(record.get("when"))
    if seen and now - seen > STALE_TUNERS:
        found.append(Finding(NOTE, "kia last updated Plex over two days ago", "It does after every refresh, so kia may have stopped running.",
                             ["state/plex-tuners.json"], "Check that kia is running: docker compose ps, in the kia folder."))  # fmt: skip
    return found


# Showing it


MARK = {PROBLEM: "PROBLEM", NOTE: "NOTE", OK: "OK"}


def render(reports: list[Report]) -> str:
    """Plain text for a terminal: each finding, what shows it, and what to do."""
    lines = []
    for r in reports:
        lines.append(r.subject)
        for f in r.findings:  # in the order they were found: the stream, then its guide
            lines.append(f"  {MARK[f.level]:<8} {f.title}")
            for label, text in (("", f.detail), ("From: ", ", ".join(f.evidence)), ("Do: ", f.fix)):
                if text:
                    lines.append(f"           {label}{text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
