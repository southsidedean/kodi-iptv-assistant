"""The 0.6.7 review's fixes to kia doctor, the MCP server, and the show search."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from kia import doctor, mcp, shows, tls
from kia.config import load

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)


def test_kia_docs_sections_skip_code_blocks():
    text = "# Setup\n\nRun this:\n\n```sh\n# With Docker\ndocker compose up -d\n```\n\n#EXTM3U isn't a heading\n## Next\nmore\n"
    assert [h for h, _ in mcp._sections(text)] == ["Setup", "Next"]
    assert "# With Docker" in mcp._sections(text)[0][1]  # kept as the section's text


def test_more_logins_are_hidden():
    for url in ("https://x/a?api_key=SECRET", "https://x/a?X-Plex-Token=SECRET&b=1", "https://x/a?access_token=SECRET",
                "https://x/a?passwd=SECRET", "https://x/a?apikey=SECRET",
                "https://x/?u=h%2Fget.php%3Fusername%3Dme%26password%3DSECRET%26type%3Dm3u"):  # fmt: skip
        assert "SECRET" not in doctor.hide_logins(url), url
    assert doctor.hide_logins("https://x/a?keyword=news") == "https://x/a?keyword=news"


def test_an_interrupted_renewal_is_repaired(tmp_path: Path):
    tls.make_self_signed(tmp_path, ["kia.lan"])
    (tmp_path / tls.MARK).unlink()  # stopped before the mark was written: still kia's own
    assert tls.info(tmp_path)["generated"]
    other = tmp_path / "other"
    tls.make_self_signed(other, ["kia.lan"])
    (other / tls.KEY).replace(tmp_path / tls.KEY)  # a new key next to the old certificate
    assert tls.info(tmp_path)["key_matches"] is False
    said = []
    assert tls.ensure(tmp_path, ["kia.lan"], said.append)["key_matches"] is True
    assert any("didn't match" in m for m in said)


class Plex:
    def __init__(self, answers):
        self.answers = answers

    def dvrs(self):
        return [{"epgIdentifier": "tv.plex.providers.epg.cloud:5"}]

    def call(self, method, path, params=None):
        return next((v for k, v in self.answers.items() if k in path), {})


def test_plex_on_now_is_only_what_has_started():
    ts = int(NOW.timestamp())
    later = {"title": "News", "Media": [{"beginsAt": ts + 3000, "endsAt": ts + 6600, "channelTitle": "2.1 WXYZ", "channelVcn": "2.1"}]}
    odd = {"title": "Odd", "Media": [{"beginsAt": ts, "endsAt": "soon", "channelTitle": "2.1 WXYZ"}]}
    [ch] = shows.plex_on_now({}, "2.1", NOW, plex=Plex({"grid": {"MediaContainer": {"Metadata": [later, odd]}}}))
    assert ch["on_now"] is None and [n["title"] for n in ch["next"]] == ["News"]  # a gap, then the news


def test_show_search_finds_accented_words_in_any_case(tmp_path: Path):
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/a.m3u\n")
    out = tmp_path / "output"
    out.mkdir()
    t = lambda h: NOW.replace(hour=h).strftime("%Y%m%d%H%M%S +0000")
    (out / "guide.xml").write_text(f'<tv>\n  <programme start="{t(17)}" stop="{t(19)}" channel="A"><title>FÚTBOL EN VIVO</title></programme>\n</tv>\n')
    cfg = load(tmp_path / "kia.toml")
    assert [s["title"] for s in shows.search(cfg, "fútbol", now=NOW, env={})["shows"]] == ["FÚTBOL EN VIVO"]
    # Without a guide of Plex's own, a category search says nothing about Plex.
    plex = Plex({})
    plex.dvrs = lambda: [{"epgIdentifier": "tv.plex.providers.epg.xmltv:8"}]
    notes = shows.search(cfg, "", "sports", now=NOW, env={}, plex=plex).get("notes", [])
    assert not any("Plex" in n for n in notes)


def test_the_full_guide_goes_with_any_playlist(tmp_path: Path):
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/a.m3u\n")
    (tmp_path / "output").mkdir()
    for name in ("tv.m3u", "guide.xml"):
        (tmp_path / "output" / name).write_text("#EXTM3U\n")
    cfg = load(tmp_path / "kia.toml")
    found = doctor._jellyfin_guides(["guide.xml"], ["tv.m3u"], cfg, [])
    assert not any(f.level == doctor.PROBLEM for f in found)


def test_kia_mcp_answers_with_broken_settings_and_odd_bytes(tmp_path: Path):
    (tmp_path / "kia.toml").write_text("x = [")  # broken
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                                                          "clientInfo": {"name": "t", "version": "1"}}}  # fmt: skip
    stdin = json.dumps(init).encode() + b"\n\xff\xfe not utf-8\n"
    r = subprocess.run([sys.executable, "-m", "kia", "mcp", "-c", str(tmp_path / "kia.toml")], input=stdin,
                       capture_output=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
                       timeout=30, check=False)  # fmt: skip
    lines = [json.loads(x) for x in r.stdout.decode().splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] and lines[1]["error"]["code"] == -32700, r.stderr.decode()
