"""The 0.6.7 review's fixes to the nightly run, kia's settings, the Dashboard and its API,
channel numbers, and the guide."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kia import alerts, api, auth, cli, doctorview, epgmap, guideview, numbers, xmltv
from kia.config import parse_sources
from kia.epgmap import SiteChannel
from kia.m3u import Entry


@pytest.mark.parametrize(("codes", "expected"), [({"check": 3, "grab": 1}, 1), ({"grab": 1, "guide": 3}, 1),
                                                 ({"check": 3}, 3), ({"grab": 1}, 1)])  # fmt: skip
def test_a_failed_download_counts_over_an_alert(tmp_path: Path, monkeypatch, codes, expected):
    (tmp_path / "kia.toml").write_text("")
    (tmp_path / "sources.txt").write_text("https://example.com/a.m3u\n")
    monkeypatch.setattr(cli, "main", lambda argv: codes.get(argv[0], 0))
    assert cli.run_pipeline(str(tmp_path / "kia.toml"), False) == expected


@pytest.mark.parametrize("value", ["inf", "nan", "1e12", "70000"])
def test_env_numbers_out_of_range_are_one_line(monkeypatch, value):
    monkeypatch.setenv("KIA_PORT", value)
    args = type("A", (), {"port": None})()
    with pytest.raises(ValueError, match="KIA_PORT"):
        cli.env_default(args)


def test_a_mistyped_ntfy_address_isnt_logged():
    said = []
    alerts.send_ntfy("ntfy.sh/my-private-topic", "run_failed", "t", "m", said.append)
    assert said and "my-private-topic" not in said[0] and "https://" in said[0]


def test_passwords_with_spaces_at_their_ends(tmp_path: Path):
    a = auth.Auth(tmp_path)
    with pytest.raises(ValueError, match="spaces"):
        a.change("  long-enough-pass ")
    said = []
    auth.Auth(tmp_path, env_password=" long-enough-pass").ensure(said.append)
    assert any("space" in m for m in said)


def test_wrong_guesses_count_by_ipv6_network():
    assert api._who("2001:db8:1:2:aaaa::1") == api._who("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert api._who("192.0.2.5") == "192.0.2.5" and api._who(None) is None


def test_open_addresses_hide_logins_in_the_path():
    assert api.mask_url("http://h/playlist/user/pass/m3u_plus") == "http://h/•••/m3u_plus"
    assert api.mask_url("http://u:p@[fd00::5]:8080/x.m3u") == "http://•••@[fd00::5]:8080/x.m3u"
    assert api.mask_url("https://example.org/list.m3u") == "https://example.org/list.m3u"


def test_a_number_in_your_own_file_wins_over_the_dashboards(tmp_path: Path):
    alpha = Entry("Alpha", "http://a/1", {"tvg-id": "Alpha.us"}, pinned_number=5, pinned_by="page")
    bravo = Entry("Bravo", "http://a/2", {"tvg-id": "Bravo.us"}, pinned_number=5, pinned_by="file")
    numbers.ChannelNumbers(tmp_path / "n.json").assign([alpha, bravo], "2026-10-25T00:00:00+00:00")
    assert bravo.attrs["tvg-chno"] == "5" and alpha.attrs["tvg-chno"] != "5"


def test_kia_toml_values_checked_at_start(tmp_path: Path):
    from kia.config import load

    (tmp_path / "sources.txt").write_text("https://example.com/a.m3u\n")
    for text, match in (("[check]\ntimeout = nan\n", "number"), ("[check]\nrequire_video = \"false\"\n", "true or false"),
                        ("[dns]\nverify_resolvers = \"8.8.8.8\"\n", "list"), ("[output]\nhttps_port = 70000\n", "65535")):  # fmt: skip
        (tmp_path / "kia.toml").write_text(text)
        with pytest.raises(ValueError, match=match):
            load(tmp_path / "kia.toml")


def test_source_names_are_safe_file_names():
    assert [s.name for s in parse_sources("https://e.com/my%20list.m3u\nhttps://e.com/a+b.m3u\n")] == ["my-20list", "a-b"]
    with pytest.raises(ValueError, match="source name"):
        parse_sources("https://e.com/x.m3u ../../out")


def test_xmltv_times_that_stop_early():
    assert xmltv.parse_time("202610241200 +0000").hour == 12
    assert xmltv.parse_time("20261024").hour == 0 and xmltv.parse_time("20261024120000-0500").utcoffset().total_seconds() == -18000
    with pytest.raises(ValueError):
        xmltv.parse_time("soon")


def test_a_name_match_you_turned_off_isnt_suggested():
    # The site's own channels say which country it's in; this one has no ID, and a looser name.
    sites = [SiteChannel(site="a.tv", lang="en", xmltv_id="CNN.us", site_id="0", name="CNN"),
             SiteChannel(site="a.tv", lang="en", xmltv_id="", site_id="1", name="Adult Swim - West")]  # fmt: skip
    names = {"AdultSwim.us@West": "Adult Swim West"}
    assert epgmap.suggest_by_name(["AdultSwim.us@West"], names, sites, set()) != []
    assert epgmap.suggest_by_name(["AdultSwim.us@West"], names, sites, set(), {"AdultSwim.us@West"}) == []


def test_report_cells_stay_in_their_column():
    assert epgmap._cell("a | b\nc") == "a / b c"


def test_the_pages_scripts():
    page = guideview.SCRIPT if hasattr(guideview, "SCRIPT") else "".join(v for v in vars(guideview).values() if isinstance(v, str))
    assert "hasOwnProperty.call(c, row.dataset.id)" in page and "Array.isArray(l)" in page
    view = "".join(v for v in vars(doctorview).values() if isinstance(v, str) and "<script>" in v)
    assert "r.json().catch(" in view  # nginx's page while kia restarts isn't JSON
    if not shutil.which("node"):
        pytest.skip("needs Node.js")
    pattern = re.search(r"var DOC_PATH = (/.*?/);", view).group(1)
    js = f"var p = {pattern}; console.log(JSON.stringify(['docs/plex.md#x', 'docs/cookbooks/plex-box.md', 'docs/../../x.md'].map(s => p.test(s))))"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [True, True, False]
