import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kia import cli
from kia.m3u import Entry, parse
from kia.numbers import ChannelNumbers
from kia.probe import ProbeResult


def tv(name: str) -> Entry:
    return Entry(name, f"http://x/{name}", {"tvg-id": f"{name}.us"})


def radio(name: str) -> Entry:
    return Entry(name, f"http://r/{name}", {"tvg-id": f"radio.{name}", "radio": "true"})


def numbers(entries: list[Entry]) -> dict[str, str]:
    return {e.name: e.attrs["tvg-chno"] for e in entries}


def test_numbers_stick_and_are_never_reused(tmp_path: Path):
    path = tmp_path / "channel-numbers.json"
    night1 = [tv("A"), tv("B"), tv("C"), radio("KINK")]
    ChannelNumbers(path).assign(night1, "2026-10-19T09:00:00+00:00")
    assert numbers(night1) == {"A": "1", "B": "2", "C": "3", "KINK": "1"}  # radio counts separately

    n = ChannelNumbers(path)
    n.assign(night1, "2026-10-19T09:00:00+00:00")
    n.save()
    night2 = [tv("A"), tv("AA"), tv("C")]  # B dropped out, AA is new
    ChannelNumbers(path).assign(night2, "2026-10-20T09:00:00+00:00")
    assert numbers(night2) == {"A": "1", "AA": "4", "C": "3"}

    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    n = ChannelNumbers(path)
    n.numbers["tv"]["B.us"]["last_seen"] = old
    n.forget_older_than(30)
    n.save()
    assert "B.us" not in json.loads(path.read_text())["numbers"]["tv"]
    back = [tv("B")]
    ChannelNumbers(path).assign(back, "2026-11-30T09:00:00+00:00")
    assert numbers(back) == {"B": "4"}  # 2 stays retired; 4 was never saved, so it's next


def test_check_writes_numbers_to_the_playlist(tmp_path: Path, monkeypatch):
    async def fake_probe(url, **kw):
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    (tmp_path / "list.m3u").write_text('#EXTM3U\n#EXTINF:-1 tvg-id="B.us",B\nhttp://b/1\n#EXTINF:-1 tvg-id="A.us",A\nhttp://a/1\n')
    (tmp_path / "sources.txt").write_text(str(tmp_path / "list.m3u") + "\n")
    (tmp_path / "kia.toml").write_text('[check]\nffprobe = "true"\n')
    assert cli.main(["check", "-c", str(tmp_path / "kia.toml")]) == 0
    assert numbers(parse((tmp_path / "output" / "curated.m3u").read_text())) == {"A": "1", "B": "2"}

    (tmp_path / "kia.toml").write_text('[check]\nffprobe = "true"\n[output]\nchannel_numbers = false\n')
    assert cli.main(["check", "-c", str(tmp_path / "kia.toml")]) == 0
    assert "tvg-chno" not in (tmp_path / "output" / "curated.m3u").read_text()


def pinned(e: Entry, n: int) -> Entry:
    e.pinned_number = n
    return e


def test_pinned_numbers_win_and_others_move(tmp_path: Path):
    path = tmp_path / "channel-numbers.json"
    n = ChannelNumbers(path)
    first = [radio(x) for x in ("Alpha", "Bravo", "KINK")]
    n.assign(first, "2026-10-19T09:00:00+00:00")
    n.save()
    assert numbers(first) == {"Alpha": "1", "Bravo": "2", "KINK": "3"}

    # KINK pinned to 1 and its sister station to 2: Alpha and Bravo move to new numbers.
    logs = []
    second = [radio("Alpha"), radio("Bravo"), pinned(radio("KINK"), 1), pinned(radio("KINK DNA"), 2)]
    n = ChannelNumbers(path)
    n.assign(second, "2026-10-20T09:00:00+00:00", logs.append)
    n.save()
    assert numbers(second) == {"Alpha": "4", "Bravo": "5", "KINK": "1", "KINK DNA": "2"}
    assert len(logs) == 2  # each move is logged

    # Removing a pin keeps the number; the next new channel skips every number in use.
    third = [radio("Alpha"), radio("Bravo"), radio("KINK"), pinned(radio("KINK DNA"), 2), radio("New")]
    ChannelNumbers(path).assign(third, "2026-10-21T09:00:00+00:00")
    assert numbers(third) == {"Alpha": "4", "Bravo": "5", "KINK": "1", "KINK DNA": "2", "New": "6"}


def test_duplicate_pins_number_the_second_automatically(tmp_path: Path):
    logs = []
    entries = [pinned(tv("A"), 7), pinned(tv("B"), 7)]
    ChannelNumbers(tmp_path / "n.json").assign(entries, "2026-10-19T09:00:00+00:00", logs.append)
    assert numbers(entries) == {"A": "7", "B": "1"} and "both" in logs[0]


def test_only_your_own_files_pin_numbers(tmp_path: Path, monkeypatch):
    async def fake_probe(url, **kw):
        return ProbeResult(ok=True, height=720)

    monkeypatch.setattr(cli, "probe", fake_probe)
    monkeypatch.delenv("KIA_SOURCES_FILE", raising=False)
    mine = tmp_path / "mine.m3u"
    mine.write_text('#EXTM3U\n#EXTINF:-1 tvg-id="Mine.us" tvg-chno="50",Mine\nhttp://m/1\n')
    (tmp_path / "kia.toml").write_text('[check]\nffprobe = "true"\n')
    (tmp_path / "sources.txt").write_text(f"{mine} mine\n")
    cfg = cli.load(tmp_path / "kia.toml")
    remote = cfg.sources[0].__class__("web", "https://example.com/list.m3u")
    assert cfg.sources[0].is_local_file and not remote.is_local_file

    monkeypatch.setattr(cli, "fetch", lambda url: '#EXTM3U\n#EXTINF:-1 tvg-id="Web.us" tvg-chno="50",Web\nhttp://w/1\n'
                        if url.startswith("https") else mine.read_text())  # fmt: skip
    cfg.sources.append(remote)
    entries = cli.load_entries(cfg, None)
    assert {e.name: e.pinned_number for e in entries} == {"Mine": 50, "Web": None}
    assert "tvg-chno" not in entries[1].attrs


def test_recycled_numbers_only_after_the_wait(tmp_path: Path):
    path = tmp_path / "n.json"
    a, b = Entry("A", "u1", {"tvg-id": "A.us"}), Entry("B", "u2", {"tvg-id": "B.us"})
    n = ChannelNumbers(path)
    n.assign([a, b], "2026-01-01T00:00:00+00:00")
    n.forget_older_than(30)  # A (1) and B (2) were last seen long before today: both retired
    n.save()
    # Without recycling, a new channel gets a new number.
    c = Entry("C", "u3", {"tvg-id": "C.us"})
    fresh = ChannelNumbers(path)
    fresh.assign([c], "2026-06-01T00:00:00+00:00")
    assert c.attrs["tvg-chno"] == "3"
    # With recycling, after the wait, it takes the lowest retired number.
    d = Entry("D", "u4", {"tvg-id": "D.us"})
    reuse = ChannelNumbers(path, recycle_after_days=90)
    reuse.assign([d], "2026-02-15T00:00:00+00:00")  # only 45 days after A was last seen
    assert d.attrs["tvg-chno"] == "3"
    e = Entry("E", "u5", {"tvg-id": "E.us"})
    later = ChannelNumbers(path, recycle_after_days=90)
    later.assign([e], "2026-06-01T00:00:00+00:00")
    assert e.attrs["tvg-chno"] == "1" and "1" not in later.retired["tv"]


def test_renumber_from_one_keeping_pins(tmp_path: Path):
    path = tmp_path / "n.json"
    es = [Entry(x, f"u{x}", {"tvg-id": f"{x}.us"}) for x in "ABCD"] + [Entry("K", "uk", {"radio": "true"})]
    n = ChannelNumbers(path)
    n.next = {"tv": 40, "radio": 9}
    n.assign(es, "2026-01-01T00:00:00+00:00")
    assert [e.attrs["tvg-chno"] for e in es] == ["40", "41", "42", "43", "9"]
    es[2].pinned_number = 1  # C was chosen to be number 1
    n.renumber(es, "2026-01-02T00:00:00+00:00")
    assert [e.attrs["tvg-chno"] for e in es] == ["2", "3", "1", "4", "1"]


def test_a_forgotten_number_above_the_counter_isnt_handed_out(tmp_path: Path):
    path = tmp_path / "channel-numbers.json"
    n = ChannelNumbers(path)
    n.assign([pinned(tv("A"), 5), tv("B")], "2026-10-19T09:00:00+00:00")  # B gets 1; the counter is at 2
    n.numbers["tv"]["A.us"]["last_seen"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    n.forget_older_than(30)  # A's 5 is retired
    new = [tv(name) for name in ("B", "C", "D", "E", "F")]
    n.assign(new, "2026-11-30T09:00:00+00:00")
    assert numbers(new) == {"B": "1", "C": "2", "D": "3", "E": "4", "F": "6"}  # 5 is skipped
