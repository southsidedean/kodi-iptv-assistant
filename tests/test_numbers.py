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
