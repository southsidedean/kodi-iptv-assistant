import json
from pathlib import Path

import pytest

from kia import cli, profiles, status
from kia.m3u import Entry, parse
from kia.probe import ProbeResult


def entry(name, group="", tid=None, source="us", radio=False) -> tuple[Entry, set[str]]:
    attrs = {"tvg-id": tid or f"{name}.us", "group-title": group}
    if radio:
        attrs["radio"] = "true"
    return Entry(name, f"http://s/{name}", attrs, source=source), {source}


ITEMS = [
    entry("Cartoon Fun", "Kids;Animation"),
    entry("Baby Shark TV", "Kids", source="samsung-us"),
    entry("CNN", "News"),
    entry("QVC", "Shop"),
    entry("Fox News Now", "News", source="samsung-us"),
    entry("KINK", "Alternative", tid="radio.kink", source="kink", radio=True),
]


def names(p: profiles.Profile) -> list[str]:
    return [e.name for e in profiles.select(p, ITEMS)]


def test_rules():
    assert names(profiles.Profile("kids", groups=["kids"], radio=False)) == ["Cartoon Fun", "Baby Shark TV"]
    assert names(profiles.Profile("bedroom", exclude_groups=["Shop"], radio=False)) == [
        "Cartoon Fun", "Baby Shark TV", "CNN", "Fox News Now"]  # fmt: skip
    # Includes add up; a pinned channel comes in even when excluded.
    dad = profiles.Profile("dad", sources=["samsung-us"], names=["^cnn$"], channels=["QVC.us"], exclude_groups=["Kids", "Shop"])
    assert names(dad) == ["CNN", "QVC", "Fox News Now"]
    assert names(profiles.Profile("radio", tv=False)) == ["KINK"]


def test_merged_channels_count_as_in_each_playlist():
    merged = [(Entry("CBS News 24/7", "u", {"tvg-id": "CBS.us"}, source="us"), {"us", "samsung-us"})]
    assert profiles.select(profiles.Profile("s", sources=["samsung-us"]), merged)


def test_load_checks_the_file(tmp_path: Path):
    f = tmp_path / "profiles.local.toml"
    assert profiles.load(f) == []  # no file, no profiles
    f.write_text('[kids]\ngroups = ["Kids"]\ntitle = "The kids"\n[bedroom-tv]\nexclude_groups = ["Shop"]\n')
    loaded = profiles.load(f)
    assert [(p.name, p.label, p.file) for p in loaded] == [("kids", "The kids", "kids.m3u"), ("bedroom-tv", "Bedroom tv", "bedroom-tv.m3u")]
    for bad, why in [('[Kids]\n', "lowercase"), ('[guide]\n', "already used"), ('[kids]\ngroup = ["x"]\n', "unknown"),
                     ('[kids]\nnames = ["("]\n', "pattern"), ('kids = 1\n', "section")]:  # fmt: skip
        f.write_text(bad)
        with pytest.raises(ValueError, match=why):
            profiles.load(f)


def test_check_writes_profile_playlists(tmp_path: Path, monkeypatch):
    async def fake_probe(url, **kw):
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    for name in ("KIA_SOURCES_FILE", "KIA_PROFILES_FILE", "KIA_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "list.m3u").write_text(
        '#EXTM3U\n#EXTINF:-1 tvg-id="Toons.us" group-title="Kids",Toons\nhttp://a/1\n'
        '#EXTINF:-1 tvg-id="News.us" group-title="News",News\nhttp://a/2\n'
    )
    (tmp_path / "sources.txt").write_text(f"{tmp_path / 'list.m3u'} us\n")
    (tmp_path / "kia.toml").write_text('[check]\nffprobe = "true"\n[output]\npublic_url = "http://tv.lan:8780"\n')
    (tmp_path / "profiles.local.toml").write_text('[kids]\ngroups = ["Kids"]\n[old]\n')
    cfg = str(tmp_path / "kia.toml")
    out = tmp_path / "output"
    assert cli.main(["check", "-c", cfg]) == 0
    kids = parse((out / "kids.m3u").read_text())
    full = {e.name: e.attrs["tvg-chno"] for e in parse((out / "curated.m3u").read_text())}
    assert [e.name for e in kids] == ["Toons"] and kids[0].attrs["tvg-chno"] == full["Toons"]  # same number
    assert 'x-tvg-url="http://tv.lan:8780/guide.xml.gz"' in (out / "kids.m3u").read_text()  # same guide
    assert (out / "old.m3u").exists()

    (tmp_path / "profiles.local.toml").write_text('[kids]\ngroups = ["Kids"]\n')  # "old" removed
    assert cli.main(["check", "-c", cfg]) == 0
    assert not (out / "old.m3u").exists() and (out / "kids.m3u").exists()

    page = status.render_html(status.collect(out, tmp_path / "state", {}, "http://tv.lan:8780"))
    assert "http://tv.lan:8780/kids.m3u" in page and "Kids" in page

    (tmp_path / "profiles.local.toml").write_text("[kids\n")  # a typo: previous playlists stay
    assert cli.main(["check", "-c", cfg]) == 0
    assert (out / "kids.m3u").exists() and json.loads((out / "profiles.json").read_text())["error"]
    page = status.render_html(status.collect(out, tmp_path / "state", {}, "http://tv.lan:8780"))
    assert "profiles file has a mistake" in page
