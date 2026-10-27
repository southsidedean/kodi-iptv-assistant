"""kia doctor: plain answers to "why?", from what kia has already written down.

Every refresh leaves a record: the Dashboard's status.json, the stream report, each
stream's history, the guide match, each guide site's log, the stack's tiles. The answers
to most questions ("why does this channel show only its name?", "why did last night's
refresh fail?") are in those files; doctor reads them and says what they mean, with the
file each answer comes from and what to do about it. It changes nothing, and gives the
same answer every time for the same files, so it can be tested. Two topics use the network,
and only to ask kia's own stack: hardware asks the stack's Prometheus, which keeps the
computer's numbers, and jellyfin asks Jellyfin, with kia's API key, what it has set up.

    kia doctor                  how kia is doing, and anything that needs you
    kia doctor channel <name>   one channel: its stream, and its guide
    kia doctor guide            the whole guide: how old, where it comes from, what's missing
    kia doctor pluto            Pluto TV through kia's fix: what's playing, what fell back
    kia doctor setup            kia's own setup: certificate, password, alerts, space, address
    kia doctor plex             why Plex shows no channels, or too few
    kia doctor hardware         the computer: disks, RAID, memory, heat, power
    kia doctor jellyfin         why Jellyfin shows no channels, or no guide
    kia doctor ... --json       the same, for scripts (and, later, kia's MCP server)

Everything it reads was written by kia, but parts of it come from strangers (channel
names, guide sites' errors), so it's shown as text and never run or followed.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from collections import Counter
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import channels, m3u, native, playlistguide
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
    # (Also with a prefix: api_key, X-Plex-Token, access_token, apikey.)
    (re.compile(r"(?i)([?&;](?:[\w-]*[_-])?(?:user(?:name)?|login|pass(?:wd|word)?|pwd|pw|token|key|apikey|auth|secret)=)[^&\s#]*"),
     r"\1•••"),
    # The same, percent-encoded inside another address ("%3Fusername%3Dme%26password%3Dsecret").
    (re.compile(r"(?i)((?:%3F|%26|%3B)(?:[\w-]*[_-])?(?:user(?:name)?|login|pass(?:wd|word)?|pwd|pw|token|key|apikey|auth|secret)%3D)"
                r".*?(?=%26|%3B|&|\s|#|$)"), r"\1•••"),
    (re.compile(r"(?i)(/(?:live|movie|series|timeshift)/)[^/\s?#]+/[^/\s?#]+/"), r"\1•••/•••/"),
    (re.compile(r"(?i)(%3A%2F%2F)[^/\s?#]*?%40"), r"\1•••%40"),
    # Without a scheme, "me:secret@host:8080/...": a name, a colon, no space, then @ and a
    # host (an email address has no colon before its @, so it's left alone). Up to the last @
    # before the host, so a password with an @ in it is hidden whole.
    (re.compile(r"(?<![\w.%+:/-])[\w.%+-]+:[^\s/]+@(?=[\w.-]+(?::\d+)?(?:[/?#\s]|$))"), "•••@"),
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


def _number(value: object) -> float:
    """A count or a number of hours from a file, or 0 when it's anything else (text, a list)."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


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
    report.findings += [f for f in guide(cfg, now).findings if f.level == PROBLEM]
    report.findings += [f for f in pluto(cfg, now).findings if f.level == PROBLEM]
    report.findings += [f for f in setup(cfg, now).findings if f.level == PROBLEM]
    # Plex's problems too, when kia feeds a Plex (its notes and all-clear stay in `kia doctor plex`),
    # and the hardware's, when Prometheus answered kia's last check of the stack.
    report.findings += [f for f in plex(cfg).findings if f.level == PROBLEM]
    if _answering(cfg).get("prometheus"):
        report.findings += [f for f in hardware(cfg).findings if f.level == PROBLEM]
    if os.environ.get("KIA_JELLYFIN_URL") and os.environ.get("KIA_JELLYFIN_TOKEN"):
        report.findings += [f for f in jellyfin(cfg, now=now).findings if f.level == PROBLEM]
    if not report.findings or all(f.level == OK for f in report.findings):
        stack = ", and everything in the stack answers" if _answering(cfg) else ""  # nothing runs next to kia: no stack to speak of
        report.findings.append(Finding(OK, "Nothing needs you", f"No alerts, no playlist whose streams all fail{stack}."))
    return report


def _refresh(status: dict, now: datetime) -> list[Finding]:
    result = status.get("result")
    last = status.get("last_run") if isinstance(status.get("last_run"), dict) else {}
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
        ok, flaky, dead = (_number(s.get(k)) for k in ("ok", "flaky", "dead"))
        if ok + flaky == 0 and dead:
            found.append(Finding(PROBLEM, f"None of the streams in {s.get('name')} work",
                                 f"All {dead} failed in the last check. The playlist may be out of date, or its server may be turning kia away.",
                                 ["output/status.json"], f"{DOCS}#a-channel-wont-play"))  # fmt: skip
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
    # A row whose address, name, ID, or status isn't text is damaged: left out, not trusted.
    streams = [s for s in list_of((_json(out / "report.json") or {}).get("streams"), dict)
               if all(isinstance(s.get(k, ""), str) for k in ("url", "name", "tvg_id", "status"))]  # fmt: skip
    removed = {k: v for k, v in channels.load(cfg.state_dir)["removed"].items() if isinstance(v, dict)}
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
        names = ", ".join(_quote(e.name, 80) for e in found[: MAX_MATCHES * 2])
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
    history = _histories(cfg).get(chosen.get("url", ""), {}) if chosen else {}
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
        hours = _number(c.get("hours_24h"))
        level, title = (OK, "It has a full guide") if hours >= 20 else (NOTE, "It has a partial guide")
        detail = f"From {site}: {hours} of the next 24 hours, {c.get('programs')} programs."
        if c.get("ends"):
            detail += f" Its listings run to {readable(c['ends'])}"
            # Only the sources the quick refreshes download again get more during the day.
            quick = site in native.QUICK_GRABBERS or str(site).startswith(playlistguide.PREFIX)
            detail += ", and the quick refreshes add more as the day goes on." if quick else ", until tonight's refresh."
        return [Finding(level, title, detail, evidence)]
    # Matched, but none of its sources had anything for the next 24 hours.
    found = [Finding(PROBLEM, "Its guide sources sent nothing for the next day",
                     f"Matched to {', '.join(candidates) or 'the upstream server'}, but none of them had listings for it, so it shows only its name.",
                     evidence, "docs/guide-under-the-hood.md#when-a-channels-guide-is-wrong-or-missing")]  # fmt: skip
    streaks = (_json(cfg.state_dir / "alert-state.json") or {}).get("site_failures")
    streaks = streaks if isinstance(streaks, dict) else {}
    for site in candidates:
        # The site's name comes from a guide source; it's only a file name here if it's a plain one.
        lines = _log_errors(cfg.epg.work_dir / "logs" / f"{site}.log") if re.fullmatch(r"[\w.-]+", site) else []
        if streaks.get(site) or lines:
            failed = f"It has failed {streaks[site]} refreshes in a row. " if streaks.get(site) else ""
            found.append(Finding(NOTE, f"{_quote(site, 60)} had errors", failed + ("Its log says: " + " / ".join(lines) if lines else ""),
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


# The guide


TOP_SITES = 6  # the guide sites named in "where it comes from"; the rest are counted


def guide(cfg: Config, now: datetime | None = None) -> Report:
    """The whole guide: when it was last built, how many channels it covers, which sites it comes
    from, sites that keep failing, and channels that got nothing. From the files the last guide
    build left (output/status.json, work/guide-choices.json, work/mapping.json, the sites' logs)."""
    now = now or datetime.now(timezone.utc)
    report = Report("The guide")
    status = _json(cfg.output_dir / "status.json") or {}
    built = _when(status.get("guide_updated"))
    if not built:
        report.findings.append(Finding(NOTE, "kia hasn't built a guide yet", "The first one comes with the first refresh, which can take up to an hour.",
                                       ["output/status.json"]))  # fmt: skip
        return report
    c = status.get("coverage") if isinstance(status.get("coverage"), dict) else {}
    counts = f"{c.get('full', '?')} TV channels have a full guide, {c.get('partial', '?')} a partial one, and {c.get('none', '?')} only their name."
    if now - built > STALE_AFTER:
        report.findings.append(Finding(PROBLEM, f"The guide hasn't been rebuilt since {readable(built.isoformat())}",
                                       "kia rebuilds it every night and refreshes it every few hours, so players are showing old listings. "
                                       "kia doctor says why the last refresh failed, if it did.",
                                       ["output/status.json"], f"{DOCS}#the-dashboard-says-the-refresh-failed"))  # fmt: skip
    else:
        report.findings.append(Finding(OK, f"The guide was rebuilt on {readable(built.isoformat())}", counts, ["output/status.json"]))
    work = cfg.epg.work_dir
    choices = _json(work / "guide-choices.json")
    if choices is None:
        report.findings.append(Finding(NOTE, "Which site each channel's guide comes from isn't recorded yet",
                                       "The next guide build records it, within a few hours.", ["work/guide-choices.json"]))  # fmt: skip
        return report
    picked = {k: v.get("site") for k, v in choices.items() if isinstance(v, dict)}
    sites = sorted(((n, s) for s, n in Counter(x for x in picked.values() if isinstance(x, str) and x).items()), key=lambda x: (-x[0], x[1]))
    if sites:
        named = ", ".join(f"{_quote(s, 60)} ({n})" for n, s in sites[:TOP_SITES])
        more = f", and {len(sites) - TOP_SITES} more" if len(sites) > TOP_SITES else ""
        report.findings.append(Finding(OK, f"It comes from {len(sites)} guide source{'' if len(sites) == 1 else 's'}",
                                       f"The most channels from {named}{more}.", ["work/guide-choices.json"]))  # fmt: skip
    report.findings += _failing_sites(cfg)
    # Radio stations aren't expected to have a guide (almost none do), as in the coverage count.
    playlist = cfg.output_dir / "curated.m3u"
    radio = {e.tvg_id for e in m3u.parse(playlist.read_text(encoding="utf-8", errors="replace")) if e.is_radio} if playlist.is_file() else set()
    empty = [k for k, site in picked.items() if not site and k not in radio]
    if cfg.upstream_url:
        if empty:
            report.findings.append(Finding(NOTE, f"{len(empty)} channels got nothing from the upstream server",
                                           "They show only their name. The upstream kia's own kia doctor guide says why.",
                                           ["work/guide-choices.json"], "docs/guide-under-the-hood.md#with-an-upstream-server"))  # fmt: skip
        return report
    mapping = _json(work / "mapping.json") or {}
    matched = {k: list_of(mapping.get(k), str) for k in empty}
    nothing = {k: v for k, v in matched.items() if v}
    if nothing:
        by_site = sorted(((n, s) for s, n in Counter(s for v in nothing.values() for s in v).items()), key=lambda x: (-x[0], x[1]))
        named = ", ".join(f"{_quote(s, 60)} ({n})" for n, s in by_site[:TOP_SITES])
        report.findings.append(Finding(NOTE, f"{len(nothing)} channel{'' if len(nothing) == 1 else 's'} matched a guide site that sent nothing for the next day",
                                       f"Mostly from {named}. Those sites had nothing for them this time; kia doctor channel <name> explains one.",
                                       ["work/mapping.json", "work/guide-choices.json"],
                                       "docs/guide-under-the-hood.md#when-a-channels-guide-is-wrong-or-missing"))  # fmt: skip
    unmatched = len(empty) - len(nothing)
    if unmatched:
        report.findings.append(Finding(NOTE, f"{unmatched} channel{'' if unmatched == 1 else 's'} matched no guide source",
                                       "No guide site lists their IDs or exactly their names, so they show only their name. That's common "
                                       "for some playlists; mapping-report.html lists possible matches to check.",
                                       ["work/mapping.json", "output/mapping-report.html"], "docs/guide-under-the-hood.md#matching-kia-map"))  # fmt: skip
    return report


def _failing_sites(cfg: Config) -> list[Finding]:
    """Guide sites on a streak of failed downloads (state/alert-state.json), with their logs' last errors."""
    streaks = (_json(cfg.state_dir / "alert-state.json") or {}).get("site_failures")
    streaks = streaks if isinstance(streaks, dict) else {}
    found = []
    for site, runs in sorted(streaks.items()):
        if not isinstance(runs, int) or runs < 1 or not isinstance(site, str):
            continue
        # The site's name comes from a guide source; it's only a file name here if it's a plain one.
        lines = _log_errors(cfg.epg.work_dir / "logs" / f"{site}.log") if re.fullmatch(r"[\w.-]+", site) else []
        found.append(Finding(PROBLEM if runs >= cfg.alerts.site_fail_runs else NOTE,
                             f"{_quote(site, 60)} failed its last {runs} download{'' if runs == 1 else 's'}",
                             "Its channels use older listings, or another site's, until it works again. "
                             + ("Its log says: " + " / ".join(lines) + ". " if lines else "")
                             + "If it stays broken, add it to KIA_EXCLUDE_SITES.",
                             ["state/alert-state.json", f"work/logs/{_quote(site, 60)}.log"], "docs/configuration.md"))  # fmt: skip
    return found


# Pluto TV


FALLBACK_MINUTES = 5  # kia.pluto.FALLBACK_ALERT, in minutes: on the plain link this long raises the alert
STALE_PLUTO = timedelta(minutes=10)  # the relay rewrites pluto.json every minute while kia runs
RECENT = timedelta(days=7)  # how far back the alert log counts


def pluto(cfg: Config, now: datetime | None = None) -> Report:
    """Pluto TV through kia's fix (the relay): how many channels use it, what's playing now and on
    which session, channels on Pluto's plain link, and the fallback alerts of the last week. From
    output/status.json, output/pluto.json (written every minute), and state/alerts.log."""
    now = now or datetime.now(timezone.utc)
    report = Report("Pluto TV")
    if not cfg.pluto_relay:
        report.findings.append(Finding(NOTE, "kia's Pluto TV fix is off",
                                       "Pluto TV channels play Pluto's plain links, which can freeze or jump at ad breaks.",
                                       [".env"], "docs/pluto-under-the-hood.md"))  # fmt: skip
        return report
    status = _json(cfg.output_dir / "status.json") or {}
    names = status.get("pluto_channels") if isinstance(status.get("pluto_channels"), dict) else {}
    if not names:
        report.findings.append(Finding(NOTE, "No Pluto TV channels go through kia",
                                       "kia's playlists have no Pluto TV channels, so its fix has nothing to do.",
                                       ["output/status.json"], "docs/pluto-under-the-hood.md#which-channels-go-through-kia"))  # fmt: skip
        return report
    report.findings.append(Finding(OK, f"{len(names)} Pluto TV channel{' goes' if len(names) == 1 else 's go'} through kia's fix",
                                   "kia opens its own session with Pluto for each, so ads don't freeze or rewind them.", ["output/status.json"]))  # fmt: skip
    state = _json(cfg.output_dir / "pluto.json")
    updated = _when(state.get("updated")) if state else None
    if updated and now - updated > STALE_PLUTO:
        report.findings.append(Finding(NOTE, f"pluto.json was last written on {readable(updated.isoformat())}",
                                       "kia rewrites it every minute while it runs, so kia may have stopped.",
                                       ["output/pluto.json"], "Check that kia is running: docker compose ps, in the kia folder."))  # fmt: skip
    playing = list_of(state.get("channels"), dict) if state and not (updated and now - updated > STALE_PLUTO) else []
    if playing:
        viewers = sum(c.get("viewers") for c in playing if isinstance(c.get("viewers"), int))
        watching = (f"{viewers} device{' is' if viewers == 1 else 's are'} watching (they asked in the last 30 seconds)." if viewers
                    else "No device asked in the last 30 seconds; kia lets a channel go 2 minutes after the last request.")
        report.findings.append(Finding(OK, f"{len(playing)} playing now", watching, ["output/pluto.json"]))
    for c in playing:
        cid = c.get("id") if isinstance(c.get("id"), str) else ""
        name = _quote(names.get(cid) or cid or "?", 80)
        minutes = c.get("fallback_minutes")
        if isinstance(minutes, int):
            report.findings.append(Finding(PROBLEM if minutes >= FALLBACK_MINUTES else NOTE,
                                           f"{name} is playing without kia's fix",
                                           f"For {minutes} minute{'' if minutes == 1 else 's'}: Pluto wouldn't open a session, so kia is passing on "
                                           "its plain link, which can freeze at ad breaks. kia tries again every 5 minutes.",
                                           ["output/pluto.json"], "docs/pluto-under-the-hood.md#when-pluto-wont-play-along"))  # fmt: skip
        problems = [_quote(p.get("text")) for p in list_of(c.get("problems"), dict) if p.get("text")][-LOG_LINES:]
        if c.get("suspicious") or problems:
            flagged = "Pluto flagged kia's session as suspicious. " if c.get("suspicious") else ""
            report.findings.append(Finding(NOTE, f"{name} had problems", flagged + ("Last: " + " / ".join(problems) if problems else ""),
                                           ["output/pluto.json"], "docs/pluto-under-the-hood.md#when-pluto-wont-play-along"))  # fmt: skip
    fell_back = [t for t, event in _alert_log(cfg) if event == "pluto_fallback" and now - t <= RECENT]
    if fell_back:
        report.findings.append(Finding(NOTE, f"Pluto TV fell back to its plain links {len(fell_back)} time{'' if len(fell_back) == 1 else 's'} in the last week",
                                       f"The last on {readable(max(fell_back).isoformat())}. Now and then is normal; every day means Pluto is "
                                       "refusing kia's sessions.", ["state/alerts.log"], "docs/pluto-under-the-hood.md#when-pluto-wont-play-along"))  # fmt: skip
    return report


def _alert_log(cfg: Config) -> list[tuple[datetime, str]]:
    """Each alert in state/alerts.log (time, tab, event, tab, ...): its time and event."""
    try:
        lines = (cfg.state_dir / "alerts.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split("\t")
        when = _when(parts[0]) if len(parts) > 1 else None
        if when and when.tzinfo:
            out.append((when, parts[1]))
    return out


# kia's own setup


LOW_DISK = 1024**3  # bytes free for kia's folders: under 1 GB, the nightly run can't save what it makes
CERT_WARN = timedelta(days=30)  # kia renews its own certificate this long before it expires; yours, you renew


def setup(cfg: Config, now: datetime | None = None) -> Report:
    """kia's own setup: the secure Dashboard's certificate, the first password, where alerts go,
    free space for kia's folders, the address players are given, and stream tests that all fail."""
    now = now or datetime.now(timezone.utc)
    report = Report("kia's setup")
    status = _json(cfg.output_dir / "status.json") or {}
    secure = status.get("secure") if isinstance(status.get("secure"), dict) else {}
    tls = secure.get("tls") if isinstance(secure.get("tls"), dict) else None
    if tls:
        report.findings += _certificate(tls, now)
    if (cfg.state_dir / "admin-password.txt").exists():
        report.findings.append(Finding(NOTE, "The Dashboard still has the password kia made",
                                       "It's still saved in state/admin-password.txt. Choose your own on the secure Dashboard's "
                                       "System page, and kia deletes that file.",
                                       ["state/admin-password.txt"], "docs/secure-page.md#changing-it"))  # fmt: skip
    if not cfg.alerts.ntfy_url and not cfg.alerts.email:
        report.findings.append(Finding(NOTE, "kia has nowhere to send alerts",
                                       "A failed refresh or a guide site that keeps failing shows only on the Dashboard. "
                                       "ntfy sends them to your phone; email works too.",
                                       [".env"], "docs/alerts.md#getting-alerts-on-your-phone-ntfy"))  # fmt: skip
    try:
        free = shutil.disk_usage(cfg.output_dir).free
    except OSError:
        free = None
    if free is not None and free < LOW_DISK:
        report.findings.append(Finding(PROBLEM, f"Only {free / 1024**2:.0f} MB free for kia's folders",
                                       "The nightly refresh needs room for the playlists, the guide, and the guide sites' downloads.",
                                       ["output/"], "Delete what you don't need on that disk."))  # fmt: skip
    host = urllib.parse.urlparse(cfg.public_url or "").hostname or ""
    if host in ("", "localhost", "::1") or host.startswith("127."):
        report.findings.append(Finding(NOTE, "kia's address is this computer only",
                                       "KIA_PUBLIC_URL isn't set to an address other devices can reach, so the guide link inside the "
                                       "playlist only works on this computer. Fine for Kodi on the same computer.",
                                       [".env"], f"{DOCS}#changing-kias-address"))  # fmt: skip
    sources = list_of(status.get("sources"), dict)
    tested = [x for x in sources if isinstance(x.get("dead"), int) and x.get("dead")]
    if len(tested) >= 2 and not any(x.get("ok") for x in sources):
        report.findings.append(Finding(PROBLEM, "Every playlist's streams failed their test",
                                       "When all of them fail at once, the cause is usually this computer's network, or ffprobe, "
                                       "not the playlists.", ["output/status.json"], f"{DOCS}#the-dashboard-says-the-refresh-failed"))  # fmt: skip
    if not report.findings or all(f.level == OK for f in report.findings):
        report.findings.append(Finding(OK, "kia's setup looks right", "Its certificate, password, alerts, free space, and address.", []))
    return report


def _certificate(tls: dict, now: datetime) -> list[Finding]:
    """The secure Dashboard's certificate, as status.json records it (kia.tls.info)."""
    found, evidence = [], ["output/status.json", "tls/"]
    expires = _when(tls.get("expires"))
    if tls.get("key_matches") is False:
        found.append(Finding(PROBLEM, "The secure Dashboard's certificate and key don't match",
                             "Browsers can't open the secure page.", evidence, "docs/secure-page.md#using-your-own-certificate"))  # fmt: skip
    if expires and not tls.get("generated"):
        if expires < now:
            found.append(Finding(PROBLEM, f"Your certificate for the secure Dashboard expired on {readable(expires.isoformat())}",
                                 "kia renews only the certificate it made itself.", evidence, "docs/secure-page.md#using-your-own-certificate"))  # fmt: skip
        elif expires - now < CERT_WARN:
            found.append(Finding(NOTE, f"Your certificate for the secure Dashboard expires on {readable(expires.isoformat())}",
                                 "kia renews only the certificate it made itself, so renew this one before then.",
                                 evidence, "docs/secure-page.md#using-your-own-certificate"))  # fmt: skip
    return found


# Plex


PLEX_MAX = 480  # channels Plex takes from one tuner
THREADFINS = (("threadfin", "Threadfin"), ("threadfin-2", "Threadfin 2"))
STALE_TUNERS = timedelta(days=2)  # plex-tuners runs after every refresh, so older means it stopped


def plex(cfg: Config, env: dict | None = None, now: datetime | None = None) -> Report:
    """Why Plex shows no channels (or too few): Threadfin, its playlist, what the last
    `kia plex-tuners` found in Plex (state/plex-tuners.json), and Plex's codecs. Offline, like
    the rest: plex-tuners is the step that talks to Plex, after each refresh."""
    from kia import m3u, plex as plex_module

    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    base = cfg.config_file.parent if cfg.config_file else Path(".")
    report = Report("Plex")
    url = env.get("KIA_PLEX_URL", "")
    answering = _answering(cfg)
    # kia's last check of the stack says what runs. A Threadfin folder or a KIA_PLEX_URL left from
    # an earlier stack (after a switch to Jellyfin, say) isn't a Plex that kia feeds. Before kia's
    # first check, or without Docker, the folders and the setting are all there is to go on.
    stack = _json(cfg.output_dir / "stack.json") or {}
    known = isinstance(stack.get("services"), list)
    folders = [(f, title) for f, title in THREADFINS
               if (base / f / "conf" / "settings.json").exists() and (not known or f in answering)]  # fmt: skip
    plex_gone = bool(url) and plex_module.in_stack(url) and known and "plex" not in answering
    if plex_gone and not folders:
        report.findings.append(Finding(NOTE, "KIA_PLEX_URL names Plex in kia's stack, which isn't in it now",
                                       "If you don't use Plex any more, remove KIA_PLEX_URL from .env. Otherwise add plex to KIA_STACK.",
                                       [".env", "output/stack.json"], "docs/stack.md"))  # fmt: skip
        return report
    if not url and not folders:
        report.findings.append(Finding(NOTE, "Plex isn't set up with kia",
                                       "There's no Threadfin and no KIA_PLEX_URL, so kia isn't feeding a Plex.",
                                       [".env", "threadfin/"], "docs/plex.md"))  # fmt: skip
        return report
    for folder, title in folders:
        report.findings += _threadfin(base, folder, title, answering.get(folder), cfg, m3u)
    if not folders:
        report.findings.append(Finding(PROBLEM, "There's no Threadfin",
                                       "Plex reaches kia's channels through Threadfin, a tuner kia sets up, and none is set up here.",
                                       ["threadfin/"], "docs/plex.md#2-start-threadfin-with-kia"))  # fmt: skip
    report.findings += _plex_tuners(_json(cfg.state_dir / "plex-tuners.json"), url, now)
    if url and plex_module.in_stack(url) and not plex_gone and not plex_module.has_codecs(base):
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
            found.append(Finding(PROBLEM, f"{title} reads {_quote(name, 80)}, which doesn't exist",
                                 "Make a playlist with that name on the Dashboard (Playlists, then Make a playlist, with For Plex through Threadfin ticked).",
                                 [f"{folder}/conf/settings.json", f"output/{name}"], "docs/plex.md#1-make-a-playlist-for-plex"))  # fmt: skip
            continue
        tv = sum(1 for e in m3u.parse(path.read_text(encoding="utf-8", errors="replace")) if not e.is_radio)
        if tv > PLEX_MAX:
            found.append(Finding(PROBLEM, f"{_quote(name, 80)} has {tv} TV channels, more than Plex takes from one tuner ({PLEX_MAX})",
                                 "Plex leaves the rest out. Make the playlist smaller, or split it between two Threadfins.",
                                 [f"output/{name}"], "docs/plex.md#before-you-start"))  # fmt: skip
        elif tv == 0:
            found.append(Finding(PROBLEM, f"{_quote(name, 80)} has no TV channels", "Threadfin has nothing to offer Plex.",
                                 [f"output/{name}"], "docs/plex.md#1-make-a-playlist-for-plex"))  # fmt: skip
        else:
            found.append(Finding(OK, f"{title} reads {_quote(name, 80)}: {tv} TV channels", "Within Plex's limit for one tuner.", [f"output/{name}"]))
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
    found = [Finding(OK, "Plex has kia's tuners", f"On {when}, kia added {len(tuners)} tuner{'' if len(tuners) == 1 else 's'} with {count} channels to "
                     f"DVR {_quote(record.get('dvr'))} and asked Plex to load the guide.", ["state/plex-tuners.json"])]  # fmt: skip
    seen = _when(record.get("when"))
    if seen and now - seen > STALE_TUNERS:
        found.append(Finding(NOTE, "kia last updated Plex over two days ago", "It does after every refresh, so kia may have stopped running.",
                             ["state/plex-tuners.json"], "Check that kia is running: docker compose ps, in the kia folder."))  # fmt: skip
    return found


# Hardware


PROMETHEUS = "http://prometheus:9090"  # as kia's container reaches it; KIA_PROMETHEUS_URL changes it
LOW_SPACE = 10  # percent free: under this a disk is nearly full
HOT_DISK = 60  # °C: most disks are made to run up to about this
HOT_GPU = 85  # °C: NVIDIA cards start slowing themselves down around here
WORN_SSD = 90  # percent of the writes an SSD is rated for
# The computer's own disks, as the Hardware dashboard picks them (scripts/dashboards.py's REAL_FS):
# without memory and container filesystems, and without macOS's system volumes and Time Machine's
# snapshots of the main disk, which share its space and would report it again.
_DISKS = 'fstype!~"tmpfs|overlay|squashfs|ramfs|vfat|nsfs", mountpoint!~"/System/Volumes/.+|/Volumes/com.apple.TimeMachine.+"'
_RAW = 'attribute_value_type="raw"'


def _prometheus(url: str, expr: str, timeout: float = 3) -> list[tuple[dict, float]]:
    """Prometheus's answer to one query: each series' labels and its value now. Raises OSError
    when Prometheus can't be reached or answers something else."""
    query = urllib.parse.urlencode({"query": expr})
    try:  # url is kia's own (PROMETHEUS, or KIA_PROMETHEUS_URL from .env), never a stranger's
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/v1/query?{query}", timeout=timeout) as r:  # nosec B310
            result = json.load(r)["data"]["result"]
        return [(x.get("metric") or {}, float(x["value"][1])) for x in result]
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise OSError(f"an answer kia didn't expect ({type(exc).__name__})") from None


def _answering(cfg: Config) -> dict:
    """Each service in kia's last check of the stack (output/stack.json): True if it answered."""
    stack = _json(cfg.output_dir / "stack.json") or {}
    return {x.get("service"): x.get("ok") for x in list_of(stack.get("services"), dict)}


HARDWARE_TIME = 10  # seconds for all of hardware's questions: the Dashboard's overview waits for them


def hardware(cfg: Config, env: dict | None = None, ask=None, clock=time.monotonic) -> Report:
    """The computer, from the numbers Prometheus keeps for the Hardware dashboard: free space,
    RAID, memory errors, the processor slowing for heat, power, and with their exporters each
    disk's health (SMART) and the graphics card. ask(expr) answers a query (tests pass their own)."""
    env = os.environ if env is None else env
    url = env.get("KIA_PROMETHEUS_URL") or PROMETHEUS
    ask = ask or (lambda expr: _prometheus(url, expr))
    report = Report("Hardware")
    if "prometheus" not in _answering(cfg) and not env.get("KIA_PROMETHEUS_URL"):
        report.findings.append(Finding(NOTE, "kia can't see the hardware without Prometheus",
                                       "Add the observability part to kia's stack: Prometheus keeps the computer's numbers, and kia reads them.",
                                       ["output/stack.json"], "docs/cookbooks/observability.md#1-turn-it-on"))  # fmt: skip
        return report
    try:
        node = ask('up{job="node"}')
    except OSError as exc:
        report.findings.append(Finding(PROBLEM, "Prometheus isn't answering", f"kia asked {_quote(url)} and got: {_quote(exc)}.",
                                       ["Prometheus"], "In the kia folder: docker compose ps, then docker compose logs prometheus"))  # fmt: skip
        return report

    give_up, cut = clock() + HARDWARE_TIME, []

    def series(expr: str) -> list[tuple[dict, float]]:
        """Each series' labels and value. A query that fails here, after the first one worked,
        counts as no data, and so does one asked after HARDWARE_TIME."""
        if clock() > give_up:
            cut.append(expr)
            return []
        try:
            return [(m if isinstance(m, dict) else {}, v) for m, v in ask(expr) if math.isfinite(v)]
        except OSError:
            return []

    def total(expr: str) -> float:
        return sum(v for _, v in series(expr))

    checked = []
    if any(v == 1 for _, v in node):
        checked.append("free space, RAID, memory errors, the processor's heat, and power")
        report.findings += _computer(series, total)
    else:
        report.findings.append(Finding(PROBLEM, "Prometheus can't reach node-exporter",
                                       "node-exporter measures the computer itself, so without it kia can't see its disks, memory, heat, or power.",
                                       ["Prometheus"], "docs/cookbooks/observability.md#2-the-computers-own-numbers"))  # fmt: skip
    disks = int(total("count(smartctl_device)"))
    if disks:
        checked.append(f"the health of {disks} disk{'' if disks == 1 else 's'} (SMART)")
        report.findings += _disks(series)
    gpu = series("max(DCGM_FI_DEV_GPU_TEMP)")
    if gpu:
        checked.append("the graphics card")
        if gpu[0][1] >= HOT_GPU:
            report.findings.append(Finding(NOTE, f"The graphics card is hot: {gpu[0][1]:.0f}°C",
                                           "Around this point it slows itself down. Check its fans and the air around the computer.",
                                           ["Prometheus (dcgm-exporter)"]))  # fmt: skip
    if cut:
        report.findings.append(Finding(NOTE, "Prometheus was slow, so kia didn't ask everything",
                                       f"{len(cut)} of its questions were left out after {HARDWARE_TIME} seconds. Ask again in a minute.",
                                       ["Prometheus"]))  # fmt: skip
    if checked and not any(f.level != OK for f in report.findings):
        more = "" if disks else " Each disk's health needs observability-disks, on Linux."
        report.findings.append(Finding(OK, "The hardware looks fine", f"kia checked {'; '.join(checked)}.{more}", ["Prometheus"],
                                       "docs/cookbooks/observability.md#6-the-hardware-dashboard-optional"))  # fmt: skip
    return report


def _name(labels: dict, key: str = "device") -> str:
    """A disk's or an array's name from Prometheus's labels, shown as text (the computer names them)."""
    return _quote(labels.get(key) or "?", 60)


def _computer(series, total) -> list[Finding]:
    """node-exporter's numbers: free space, RAID, memory errors, heat, and power."""
    found, node = [], ["Prometheus (node-exporter)"]
    free = {}  # by device, so a disk mounted in two places (a snap's view of /, say) is named once, by its shortest
    for labels, percent in series(f"100 * node_filesystem_avail_bytes{{{_DISKS}}} / node_filesystem_size_bytes{{{_DISKS}}}"):
        mount = _name(labels, "mountpoint")
        if labels.get("device") not in free or len(mount) < len(free[labels.get("device")][0]):
            free[labels.get("device")] = (mount, percent)
    for mount, percent in sorted(free.values()):
        if percent < LOW_SPACE:
            found.append(Finding(PROBLEM, f"{mount} is nearly full: {percent:.0f}% free",
                                 "When it fills up, kia and anything else there can't save files.", node,
                                 "Delete what you don't need there, or move it to another disk."))  # fmt: skip
    for labels, failed in series('node_md_disks{state="failed"} > 0'):
        found.append(Finding(PROBLEM, f"RAID array {_name(labels)} has {failed:.0f} failed disk{'' if failed == 1 else 's'}",
                             "The array still works, but one more failure can lose what's on it.", node,
                             "Replace the failed disk. cat /proc/mdstat shows which one it is (marked (F))."))  # fmt: skip
    for labels, _ in series('node_md_state{state=~"recovering|resync|check"} == 1'):
        found.append(Finding(NOTE, f"RAID array {_name(labels)} is rebuilding or checking itself",
                             "Its disks are slower until it finishes.", node))  # fmt: skip
    bad = total("sum(increase(node_edac_uncorrectable_errors_total[1d]))")
    fixed = total("sum(increase(node_edac_correctable_errors_total[1d]))")
    if bad >= 1:
        found.append(Finding(PROBLEM, "The memory had errors it couldn't correct in the last day",
                             "That can crash programs or damage files.", node,
                             "Test the memory (memtest86+), and replace a failing stick."))  # fmt: skip
    elif fixed >= 1:
        found.append(Finding(NOTE, f"The memory corrected {fixed:.0f} errors in the last day",
                             "ECC memory fixes these by itself, but a number that keeps growing means a stick is wearing out.", node))  # fmt: skip
    slowed = total("sum(increase(node_cpu_core_throttles_total[1h]))") + total("sum(increase(node_cpu_package_throttles_total[1h]))")
    if slowed >= 1:
        found.append(Finding(NOTE, f"The processor slowed itself down {slowed:.0f} times in the last hour",
                             "Usually for heat. Check the fans, and that air can reach the computer.", node))  # fmt: skip
    battery = series('max(node_power_supply_power_source_state{state="Battery Power"}) or '
                     '(1 - max(node_power_supply_online * on(instance, power_supply) group_left node_power_supply_info{type="Mains"}))')
    if battery and battery[0][1] == 1:
        # A wireless keyboard's or remote's battery (scope="Device") isn't the computer's.
        device = 'unless on(instance, power_supply) node_power_supply_info{scope="Device"}'
        charge = series(f"min((node_power_supply_current_capacity / node_power_supply_max_capacity) {device}) * 100 "
                        f"or min(node_power_supply_capacity {device})")
        left = series(f"min(node_power_supply_time_to_empty_seconds {device})")
        detail = ", ".join(x for x in (f"{charge[0][1]:.0f}% charged" if charge else "",
                                       f"about {left[0][1] / 60:.0f} minutes left" if left and left[0][1] > 0 else "") if x)
        low = bool(charge) and charge[0][1] < 20
        found.append(Finding(PROBLEM if low else NOTE, "The computer is running on its battery",
                             (detail[0].upper() + detail[1:] + ". " if detail else "") + "The power is off, or it's unplugged.", node,
                             "If the power is out, shut the computer down before the battery runs out." if low else ""))  # fmt: skip
    return found


def _disks(series) -> list[Finding]:
    """smartctl-exporter's numbers: each disk's own health check and the counts that grow as it wears out."""
    found, smart = [], ["Prometheus (smartctl-exporter)"]
    for labels, _ in series("max by (device) (smartctl_device_smart_status) == 0"):
        found.append(Finding(PROBLEM, f"Disk {_name(labels)} fails its own health check (SMART)", "The disk itself predicts it will fail soon.", smart,
                             "Copy what's on it to another disk, and replace it."))  # fmt: skip
    unreadable = f'sum by (device) (smartctl_device_attribute{{attribute_name=~"Current_Pending_Sector|Offline_Uncorrectable",{_RAW}}}) > 0'
    for labels, n in series(unreadable):
        found.append(Finding(PROBLEM, f"Disk {_name(labels)} has {n:.0f} sector{'' if n == 1 else 's'} it couldn't read",
                             "Some of what's on it may already be damaged, and the disk is wearing out.", smart,
                             "Copy what's on it to another disk, and plan to replace it."))  # fmt: skip
    for labels, n in series(f'max by (device) (smartctl_device_attribute{{attribute_name="Reallocated_Sector_Ct",{_RAW}}}) > 0'):
        found.append(Finding(NOTE, f"Disk {_name(labels)} has replaced {n:.0f} worn sector{'' if n == 1 else 's'}",
                             "A few is normal for an older disk; a number that keeps growing means it's wearing out.", smart))  # fmt: skip
    for labels, t in series(f'max by (device) (smartctl_device_temperature{{temperature_type="current"}}) >= {HOT_DISK}'):
        found.append(Finding(NOTE, f"Disk {_name(labels)} is hot: {t:.0f}°C", f"Most disks are made to run up to about {HOT_DISK}°C. "
                             "Check the fans, and the air around it.", smart))  # fmt: skip
    for labels, used in series(f"max by (device) (smartctl_device_percentage_used) >= {WORN_SSD}"):
        found.append(Finding(NOTE, f"Disk {_name(labels)} has used {used:.0f}% of the writes it's made for",
                             "SSDs wear out as they're written to. Plan to replace it.", smart))  # fmt: skip
    return found


# Jellyfin


STALE_GUIDE = timedelta(days=2)  # Jellyfin's Refresh Guide task runs every day, so older means it stopped
_JELLYFIN_TIME_RE = re.compile(r"(\.\d{6})\d+")  # Jellyfin's times have 7 decimals; Python 3.11 reads up to 6


def jellyfin(cfg: Config, env: dict | None = None, now: datetime | None = None, client=None) -> Report:
    """Why Jellyfin shows no channels, or no guide: its Live TV settings (the M3U tuner and the
    XMLTV guide, and whether they're kia's), its channels, and its last guide refresh. Asks
    Jellyfin with kia's API key (KIA_JELLYFIN_URL, KIA_JELLYFIN_TOKEN): Jellyfin's settings are
    hidden from kia's container, and no step of kia's writes them down. client stands in for
    kia.jellyfin.Jellyfin in tests."""
    from kia import jellyfin as jellyfin_module

    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    url, token = env.get("KIA_JELLYFIN_URL", ""), env.get("KIA_JELLYFIN_TOKEN", "")
    answering = _answering(cfg)
    report = Report("Jellyfin")
    if not url and "jellyfin" not in answering:
        report.findings.append(Finding(NOTE, "Jellyfin isn't set up with kia",
                                       "There's no Jellyfin in kia's stack and no KIA_JELLYFIN_URL, so kia doesn't know of one.",
                                       [".env", "output/stack.json"], "docs/jellyfin.md"))  # fmt: skip
        return report
    # As for Plex: a KIA_JELLYFIN_URL left from an earlier stack (http://jellyfin:8096 with no
    # Jellyfin in kia's last check of it) isn't a Jellyfin to ask.
    stack = _json(cfg.output_dir / "stack.json") or {}
    if (urllib.parse.urlparse(url).hostname == "jellyfin" and isinstance(stack.get("services"), list)
            and "jellyfin" not in answering):  # fmt: skip
        report.findings.append(Finding(NOTE, "KIA_JELLYFIN_URL names Jellyfin in kia's stack, which isn't in it now",
                                       "If you don't use Jellyfin any more, remove KIA_JELLYFIN_URL from .env. Otherwise add jellyfin to KIA_STACK.",
                                       [".env", "output/stack.json"], "docs/stack.md"))  # fmt: skip
        return report
    if answering.get("jellyfin") is False:
        report.findings.append(Finding(PROBLEM, "Jellyfin isn't answering", "kia knocked on its port and got no answer.",
                                       ["output/stack.json"], "In the kia folder: docker compose ps, then docker compose logs jellyfin"))  # fmt: skip
    if not url:
        report.findings.append(Finding(NOTE, "kia doesn't know Jellyfin's address",
                                       "KIA_JELLYFIN_URL isn't set. scripts/stack.sh apply sets it for Jellyfin in kia's stack.",
                                       [".env"], "docs/cookbooks/jellyfin-box.md#2-turn-on-jellyfin"))  # fmt: skip
        return report
    if not token:
        report.findings.append(Finding(NOTE, "kia can't check Jellyfin's Live TV without an API key",
                                       "In Jellyfin, go to Dashboard, then API Keys, and make one named kia. Put it in .env as "
                                       "KIA_JELLYFIN_TOKEN, then run docker compose up -d.",
                                       [".env"], "docs/cookbooks/jellyfin-box.md#6-see-whats-playing-optional"))  # fmt: skip
        return report
    client = client or jellyfin_module.Jellyfin(url, token, timeout=3)
    try:
        live_tv = client.call("/System/Configuration/livetv")
    except jellyfin_module.JellyfinError as exc:
        report.findings.append(Finding(PROBLEM, "kia can't ask Jellyfin", f"{_quote(exc)}.", [".env"],
                                       "Check KIA_JELLYFIN_URL and KIA_JELLYFIN_TOKEN in .env, then run docker compose up -d."))  # fmt: skip
        return report

    def ask(path: str) -> object:
        """Jellyfin's answer, or None: after the first question worked, a failure counts as no data."""
        try:
            return client.call(path)
        except jellyfin_module.JellyfinError:
            return None

    live_tv = live_tv if isinstance(live_tv, dict) else {}
    tuners = [t for t in list_of(live_tv.get("TunerHosts"), dict) if str(t.get("Type")).lower() == "m3u"]
    guides = [g for g in list_of(live_tv.get("ListingProviders"), dict) if str(g.get("Type")).lower() == "xmltv"]
    evidence = ["Jellyfin (Live TV settings)"]
    if not tuners:
        report.findings.append(Finding(PROBLEM, "Jellyfin has no M3U tuner", "So it has no channels from kia. Add kia's playlist as an M3U Tuner.",
                                       evidence, "docs/jellyfin.md#jellyfin"))  # fmt: skip
        return report
    playlists = []
    for tuner in tuners:
        report.findings += _jellyfin_tuner(_file_name(tuner.get("Url")), cfg, evidence, playlists)
    report.findings += _jellyfin_guides([_file_name(g.get("Path")) for g in guides], playlists, cfg, evidence)
    channels = ask("/LiveTv/Channels?limit=0")
    count = channels.get("TotalRecordCount") if isinstance(channels, dict) else None
    if count == 0:
        report.findings.append(Finding(PROBLEM, "Jellyfin has no channels yet",
                                       "It has a tuner but hasn't loaded its channels. Choose Refresh Guide Data on its Live TV page.",
                                       ["Jellyfin (Live TV)"], "docs/jellyfin.md#if-somethings-wrong"))  # fmt: skip
    elif isinstance(count, int):
        report.findings.append(Finding(OK, f"Jellyfin shows {count} channels", "", ["Jellyfin (Live TV)"]))
    tasks = [t for t in list_of(ask("/ScheduledTasks?isHidden=false"), dict) if t.get("Key") == "RefreshGuide"]
    if tasks:
        report.findings += _jellyfin_refresh(tasks[0].get("LastExecutionResult"), now)
    return report


def _file_name(address: object) -> str:
    """The file at the end of an address or path ("http://web/tv.m3u" -> "tv.m3u"), as text."""
    path = urllib.parse.urlparse(str(address or "")).path or str(address or "")
    return _quote(path.rstrip("/").rsplit("/", 1)[-1], 80)


def _guide_for(playlist: str) -> str:
    """The guide kia makes for a playlist: tv.m3u's is tv.guide.xml.gz; curated.m3u's, guide.xml.gz."""
    name = playlist.removesuffix(".m3u")
    return "guide.xml.gz" if name == "curated" else f"{name}.guide.xml.gz"


def _jellyfin_tuner(name: str, cfg: Config, evidence: list[str], playlists: list[str]) -> list[Finding]:
    path = cfg.output_dir / name
    if not name.endswith(".m3u") or not path.is_file():
        return [Finding(NOTE, f"Jellyfin reads {_quote(name, 80) or 'a playlist'}, which isn't one of kia's",
                        "If it's meant to be kia's, check the address: http://web/tv.m3u for Jellyfin in kia's stack, "
                        "otherwise http://<kia's address>:8780/tv.m3u.", evidence, "docs/jellyfin.md#jellyfin")]  # fmt: skip
    playlists.append(name)
    entries = m3u.parse(path.read_text(encoding="utf-8", errors="replace"))
    radio = sum(1 for e in entries if e.is_radio)
    found = [Finding(OK, f"Jellyfin reads {_quote(name, 80)}: {len(entries) - radio} TV channels", "", evidence + [f"output/{name}"])]
    if radio:
        found.append(Finding(NOTE, f"{_quote(name, 80)} has {radio} radio station{'' if radio == 1 else 's'} too",
                             "Jellyfin lists them as TV channels, numbered apart from TV, so you see two channel 1s. "
                             "Use tv.m3u, or a playlist with radio left out.",
                             [f"output/{name}"], "docs/jellyfin.md#before-you-start-the-tv-only-playlist"))  # fmt: skip
    return found


def _jellyfin_guides(names: list[str], playlists: list[str], cfg: Config, evidence: list[str]) -> list[Finding]:
    if not names:
        return [Finding(PROBLEM, "Jellyfin has no XMLTV guide", "Its channels show only their names. Add kia's guide as an XMLTV provider.",
                        evidence, "docs/jellyfin.md#jellyfin")]  # fmt: skip
    found = []
    for name in names:
        if not (cfg.output_dir / name).is_file():
            found.append(Finding(NOTE, f"Jellyfin's guide is {_quote(name, 80) or 'a file'}, which isn't one of kia's",
                                 "If it's meant to be kia's, check the address: the guide that goes with tv.m3u is tv.guide.xml.gz.",
                                 evidence, "docs/jellyfin.md#jellyfin"))  # fmt: skip
    # guide.xml.gz has every channel, so it fits any playlist; otherwise each needs its own.
    for playlist in playlists:
        if _guide_for(playlist) not in names and not {"guide.xml.gz", "guide.xml"} & set(names):
            found.append(Finding(PROBLEM, f"Jellyfin's guide doesn't go with {playlist}",
                                 f"Its channels' shows are in {_guide_for(playlist)}, which Jellyfin doesn't read, so they show only their names.",
                                 evidence, "docs/jellyfin.md#jellyfin"))  # fmt: skip
    return found


def _jellyfin_refresh(last: object, now: datetime) -> list[Finding]:
    """Jellyfin's Refresh Guide task, the last time it ran."""
    last = last if isinstance(last, dict) else {}
    end = _when(_JELLYFIN_TIME_RE.sub(r"\1", str(last.get("EndTimeUtc") or "")).replace("Z", "+00:00"))
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)  # UTC by its name; without a zone, it can't be compared
    evidence = ["Jellyfin (Scheduled Tasks)"]
    if not end:
        return [Finding(NOTE, "Jellyfin hasn't refreshed its guide yet", "Choose Refresh Guide Data on its Live TV page.",
                        evidence, "docs/jellyfin.md#jellyfin")]  # fmt: skip
    when = readable(end.isoformat())
    if last.get("Status") == "Failed":
        return [Finding(PROBLEM, "Jellyfin's last guide refresh failed", f"On {when} it said: {_quote(last.get('ErrorMessage') or 'nothing')}.",
                        evidence, "docs/jellyfin.md#if-somethings-wrong")]  # fmt: skip
    if now - end > STALE_GUIDE:
        return [Finding(NOTE, "Jellyfin last refreshed its guide over two days ago",
                        "It does every day by itself, so its Refresh Guide task may be off (Dashboard, then Scheduled Tasks).",
                        evidence, "docs/jellyfin.md#jellyfin")]  # fmt: skip
    return [Finding(OK, "Jellyfin refreshed its guide", f"On {when}.", evidence)]


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
