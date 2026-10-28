import json
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kia import guard, pluto, serve
from kia.m3u import Entry

CID = "66ba495ffe11e5000881f049"
T0 = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
SHOW = "https://siloh.pluto.tv/clip/63d1a7cb662ef90014db1315_x/1080p/{part}/hls/video/seg_{n:05d}.ts"
AD = "https://siloh.pluto.tv/v1/p(2_ad/creative/6abec2e1ac612a761b08e646_ad/video.mp4)/frag({n})/sign/v1/{sig}=/1.ts"
KEY = "https://siloh.pluto.tv/clip/63d1a7cb662ef90014db1315_x/1080p/{part}/hls/seg_keyfile_0.key"


def media(pieces: list[tuple[float, str]], seq: int = 0, key: str | None = None) -> str:
    """A Pluto media playlist: (seconds after T0, address) for each piece, 5 s each."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:5", f"#EXT-X-MEDIA-SEQUENCE:{seq}"]
    for at, uri in pieces:
        pdt = (T0 + timedelta(seconds=at)).isoformat().replace("+00:00", "Z")
        lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{pdt}")
        if key:
            lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="{key}",IV=0x00000000000000000000000000000063')
        lines += ["#EXTINF:5,", uri]
    return "\n".join(lines) + "\n"


class FakePluto:
    """Answers kia's requests the way Pluto does; `playlists` is what the stream sends next."""

    def __init__(self, playlists: list[str], boot_works: bool = True):
        self.playlists = list(playlists)
        self.boot_works = boot_works
        self.boots = 0
        self.asked: list[str] = []
        self.refresh_in = 28800  # Pluto's refreshInSec

    def __call__(self, url: str):
        self.asked.append(url)
        if url.startswith("https://boot.pluto.tv/"):
            if not self.boot_works:
                raise urllib.error.URLError("boot is down")
            self.boots += 1
            body = {"servers": {"stitcher": "https://stitcher.pluto.tv"}, "stitcherParams": "a=1",
                    "sessionToken": f"token{self.boots}", "refreshInSec": self.refresh_in, "isSuspicious": False}  # fmt: skip
            return url, json.dumps(body).encode()
        if url.endswith("high.m3u8"):
            text = self.playlists.pop(0) if len(self.playlists) > 1 else self.playlists[0]
            return url, text.encode()
        if "master.m3u8" in url or url.startswith("https://jmp2.uk/"):
            return url, b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=300\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2400\nhigh.m3u8\n"
        if url.endswith(".key"):
            return url, bytes(range(16))
        return url, b"piece:" + url.encode()


def relay(fake: FakePluto, tmp_path: Path | None = None):
    calls = []

    def fake_remux(body, key, iv, dur, t0):
        calls.append({"body": body, "key": key, "iv": iv, "dur": dur, "t0": t0})
        return b"TS" + body

    r = pluto.Relay(tmp_path / "pluto.json" if tmp_path else None, log=lambda m: None, fetcher=fake, remux_fn=fake_remux)
    return r, calls


def look(r: pluto.Relay) -> str:
    ch = r.channel(CID)
    ch.last_fetch = 0.0  # don't wait between looks
    return ch.playlist()


def numbers(playlist: str) -> list[str]:
    return [line for line in playlist.splitlines() if line.endswith(".ts")]


def test_pluto_channels_point_at_kia():
    entries = [
        Entry("Frasier", f"https://jmp2.uk/plu-{CID}.m3u8", {"tvg-id": "Frasier.us"}, vlcopts={"http-user-agent": "x"}),
        Entry("Other", "https://example.com/live.m3u8"),
        Entry("Stitcher", f"https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/v2/stitch/hls/channel/{CID}/master.m3u8"),
    ]
    out = pluto.for_players(entries, "http://192.168.1.20:8780")
    assert out[0].url == f"http://192.168.1.20:8780/pluto/{CID}.m3u8" and out[0].vlcopts == {}
    assert out[0].attrs == {"tvg-id": "Frasier.us"}
    assert out[1].url == "https://example.com/live.m3u8"
    assert out[2].url.endswith(f"/pluto/{CID}.m3u8")
    assert entries[0].url.startswith("https://jmp2.uk/")  # the originals are left alone
    assert pluto.for_players(entries, None) == entries  # no address to point at


def test_steady_numbers_and_one_clock_when_pluto_jumps():
    part = "162136-698541"
    fake = FakePluto([
        media([(0, SHOW.format(part=part, n=0)), (5, SHOW.format(part=part, n=1))], seq=40),
        media([(5, SHOW.format(part=part, n=1)), (10, SHOW.format(part=part, n=2))], seq=1234),  # garbage
        media([(10, SHOW.format(part=part, n=2)), (15, AD.format(n=960, sig="a"))], seq=7),
    ])  # fmt: skip
    r, _calls = relay(fake)
    first = look(r)
    assert "#EXT-X-MEDIA-SEQUENCE:0" in first and numbers(first) == [f"{CID}/0.ts", f"{CID}/1.ts"]
    look(r)
    last = look(r)
    assert numbers(last) == [f"{CID}/{n}.ts" for n in range(4)]
    assert "#EXT-X-KEY" not in last and "#EXT-X-DISCONTINUITY" not in last
    ch = r.channel(CID)
    assert [p.t0 for p in ch.pieces] == [10.0, 15.0, 20.0, 25.0]
    assert ch.pieces[3].clip != ch.pieces[2].clip  # the ad is its own clip


def test_repeats_and_rewinds_are_held_back_but_new_parts_play(monkeypatch):
    monkeypatch.setattr(pluto, "SESSION_GAP", -1)
    a, b = "162136-698541", "698541-901233"
    fake = FakePluto([
        media([(0, SHOW.format(part=a, n=60)), (5, SHOW.format(part=a, n=61))]),
        # Pluto goes back into the part it already sent...
        media([(10, SHOW.format(part=a, n=3)), (15, SHOW.format(part=a, n=4))]),
        # ...then the next part of the show, which starts again from 0.
        media([(20, SHOW.format(part=b, n=0)), (25, SHOW.format(part=b, n=1))]),
    ])  # fmt: skip
    r, _ = relay(fake)
    look(r)
    assert len(numbers(look(r))) == 2  # nothing from the rewind
    ch = r.channel(CID)
    assert ch.stats.rewinds == 1 and fake.boots == 2  # and kia asked for a fresh session
    assert len(numbers(look(r))) == 4
    assert [p.pos for p in ch.pieces] == [60, 61, 0, 1]


def test_the_same_piece_twice_is_dropped():
    fake = FakePluto([media([(0, AD.format(n=960, sig="a")), (3, AD.format(n=960, sig="b")), (5, AD.format(n=1383, sig="c"))])])
    r, _ = relay(fake)
    assert len(numbers(look(r))) == 2


def test_long_rewind_plays_after_the_hold(monkeypatch):
    a = "162136-698541"
    fake = FakePluto([
        media([(0, SHOW.format(part=a, n=60))]),
        media([(5, SHOW.format(part=a, n=3))]),
        media([(10, SHOW.format(part=a, n=4))]),
    ])  # fmt: skip
    r, _ = relay(fake)
    look(r)
    look(r)
    monkeypatch.setattr(pluto, "HOLD_LIMIT", -1)  # held long enough
    assert len(numbers(look(r))) == 2
    assert any("kept going back" in p["text"] for p in r.channel(CID).stats.problems)


def test_pieces_are_decrypted_once_for_every_viewer():
    part = "162136-698541"
    fake = FakePluto([media([(0, SHOW.format(part=part, n=0))], seq=99, key=KEY.format(part=part))])
    r, calls = relay(fake)
    look(r)
    status, kind, body = r.handle(f"/pluto/{CID}/0.ts", "192.0.2.30")
    assert status == 200 and kind == "video/mp2t" and body.startswith(b"TSpiece:")
    r.handle(f"/pluto/{CID}/0.ts", "192.0.2.31")
    assert len(calls) == 1
    assert calls[0]["key"] == bytes(range(16)).hex() and calls[0]["iv"] == "0" * 30 + "63"
    assert calls[0]["t0"] == pluto.START and calls[0]["dur"] == 5.0
    assert r.status()["channels"][0]["viewers"] == 2


def test_debug_capture_is_off_until_you_make_the_folder(tmp_path):
    part = "162136-698541"
    fake = FakePluto([media([(0, SHOW.format(part=part, n=0))], seq=99, key=KEY.format(part=part))])

    def boom(body, key, iv, dur, t0):
        raise ValueError("sample rate not set")

    dbg = tmp_path / "pluto-debug"
    r = pluto.Relay(tmp_path / "pluto.json", log=lambda m: None, fetcher=fake, remux_fn=boom, debug_dir=dbg)
    look(r)
    # Off by default: the folder isn't there, so a failing copy saves nothing.
    assert r.handle(f"/pluto/{CID}/0.ts", "v")[0] == 502  # the copy failed, so no piece
    assert not dbg.exists()
    # Armed: make the folder, and the next piece ffmpeg can't copy is saved, raw, with its
    # details, so a real failure can be looked at later.
    dbg.mkdir()
    r.handle(f"/pluto/{CID}/0.ts", "v")
    saved = list(dbg.glob("*.ts"))
    assert len(saved) == 1 and saved[0].read_bytes().startswith(b"piece:")
    meta = json.loads(next(dbg.glob("*.json")).read_text())
    assert meta["error"].startswith("sample rate not set") and meta["cid"] == CID
    assert meta["iv"] == "0" * 30 + "63" and meta["key"] == bytes(range(16)).hex()


def test_debug_capture_stops_at_the_cap(tmp_path, monkeypatch):
    fake = FakePluto([media([(0, SHOW.format(part="p", n=0))], seq=99, key=KEY.format(part="p"))])

    def boom(*a):
        raise ValueError("x")

    r = pluto.Relay(tmp_path / "pluto.json", log=lambda m: None, fetcher=fake, remux_fn=boom, debug_dir=tmp_path / "d")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(pluto, "DEBUG_CAPTURES", 2)
    look(r)
    for _ in range(4):
        r.handle(f"/pluto/{CID}/0.ts", "v")  # the same failing piece, over and over
    assert len(list((tmp_path / "d").glob("*.ts"))) == 2  # no more than the cap


def test_no_iv_means_the_sequence_number():
    lines = media([(0, SHOW.format(part="p", n=0))], seq=0x2A, key="k.key").replace(",IV=0x00000000000000000000000000000063", "")
    fake = FakePluto([lines])
    r, calls = relay(fake)
    look(r)
    r.handle(f"/pluto/{CID}/0.ts", "x")
    assert calls[0]["iv"] == f"{0x2A:032x}"


def test_not_a_playlist_means_a_new_session():
    good = media([(0, SHOW.format(part="p", n=0))])
    fake = FakePluto(["<html>session expired</html>", good])
    r, _ = relay(fake)
    assert len(numbers(look(r))) == 1
    assert fake.boots == 2


def test_falls_back_to_jmp2_when_pluto_wont_open_a_session(tmp_path: Path):
    fake = FakePluto([media([(0, SHOW.format(part="p", n=0))])], boot_works=False)
    r, _ = relay(fake, tmp_path)
    status, _, _ = r.handle(f"/pluto/{CID}.m3u8", "x")
    assert status == 200
    ch = r.channel(CID)
    assert not ch.session.own and ch.stats.fallbacks == 1
    assert any(u.startswith(f"https://jmp2.uk/plu-{CID}") for u in fake.asked)
    r.save_status(force=True)
    saved = json.loads((tmp_path / "pluto.json").read_text())
    assert saved["channels"][0]["own_session"] is False
    assert "jmp2.uk" in saved["channels"][0]["problems"][0]["text"]


def test_bad_addresses_and_pluto_down():
    r, _ = relay(FakePluto([""], boot_works=False))
    assert r.handle("/pluto/nope.m3u8", "x")[0] == 404
    assert r.handle(f"/pluto/{CID}/5.ts", "x")[0] == 404  # nothing to play yet
    r2, _ = relay(FakePluto([""], boot_works=False))
    r2.fetcher = lambda url: (_ for _ in ()).throw(urllib.error.URLError("down"))
    assert r2.handle(f"/pluto/{CID}.m3u8", "x")[0] == 502


def test_idle_channels_are_let_go(monkeypatch):
    r, _ = relay(FakePluto([media([(0, SHOW.format(part="p", n=0))])]))
    look(r)
    monkeypatch.setattr(pluto, "IDLE_AFTER", -1)
    assert r.status()["channels"] == []


def test_server_answers_pluto_addresses(tmp_path: Path):
    r, _ = relay(FakePluto([media([(0, SHOW.format(part="p", n=0))])]))
    srv = serve.serve_in_background(tmp_path / "www", "127.0.0.1", 0, relay=r)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/pluto/{CID}.m3u8"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.headers.get_content_type() == "application/vnd.apple.mpegurl"
            assert f"{CID}/0.ts" in resp.read().decode()
    finally:
        srv.shutdown()


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("openssl")),
                                  reason="needs ffmpeg, ffprobe and openssl")  # fmt: skip


@needs_ffmpeg
def test_remux_decrypts_and_moves_the_clock(tmp_path: Path):
    clip = tmp_path / "clip.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=30", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "3", "-c:v", "libx264", "-c:a", "aac", "-output_ts_offset", "40",
                    "-f", "mpegts", str(clip)], check=True)  # fmt: skip
    key, iv = "000102030405060708090a0b0c0d0e0f", "0" * 30 + "63"
    enc = subprocess.run(["openssl", "enc", "-aes-128-cbc", "-K", key, "-iv", iv, "-in", str(clip)],
                         check=True, capture_output=True).stdout  # fmt: skip
    out = tmp_path / "out.ts"
    out.write_bytes(pluto.remux(enc, key, iv, 2.0, 100.0))
    v, a = pluto.probe_start(str(out), None, iv)
    assert abs(v - 100.0) < 0.05 and abs(a - 100.0) < 0.1
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout  # fmt: skip
    assert float(probe) < 2.5  # cut back to the listed length


def test_pluto_channels_from_samsungs_list_use_the_relay():
    e = [Entry("Pluto TV Fantastic", "https://jmp2.uk/stvp-USBA300017PK", {"tvg-id": "PlutoTVFantastic.us@SD"}),
         Entry("Samsung News", "https://jmp2.uk/stvp-USBA9999", {"tvg-id": "News.us"}),
         Entry("Pluto TV Down", "https://jmp2.uk/stvp-USBA0000", {})]  # fmt: skip
    asked = []

    def resolve(url):
        asked.append(url)
        if url.endswith("0000"):
            raise urllib.error.URLError("down")
        return f"https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/v2/stitch/embed/hls/channel/{CID}/master.m3u8?x=1"

    assert pluto.adopt_partner_channels(e, log=lambda m: None, resolve=resolve) == 1
    assert e[0].url == f"https://jmp2.uk/plu-{CID}.m3u8"
    assert e[1].url.endswith("USBA9999") and "USBA9999" not in "".join(asked)  # not named for Pluto: not looked up
    assert e[2].url.endswith("USBA0000")  # couldn't tell: left alone
    assert pluto.for_players(e, "http://192.168.1.20:8780")[0].url == f"http://192.168.1.20:8780/pluto/{CID}.m3u8"


def test_problems_reach_kias_log_and_the_folder_answers():
    said = []
    r = pluto.Relay(None, log=said.append, fetcher=lambda u: (_ for _ in ()).throw(urllib.error.URLError("down")))
    assert r.handle("/pluto/", "x")[0] == 200
    r.handle(f"/pluto/{CID}.m3u8", "x")
    assert any(m.startswith(f"Pluto {CID}: ") and "jmp2.uk link failed" in m for m in said)


def test_counters_keep_counting_after_a_channel_goes_idle(monkeypatch):
    r, _ = relay(FakePluto([media([(0, SHOW.format(part="p", n=0))])]))
    playlist = look(r)
    piece = numbers(playlist)[0]
    status, _, body = r.handle(f"/pluto/{piece}", "tv")
    assert status == 200
    c = r.counters()
    assert (c["playing"], c["viewers"], c["sessions"], c["pieces"], c["bytes"]) == (1, {CID: 1}, 1, 1, len(body))
    monkeypatch.setattr(pluto, "IDLE_AFTER", -1)
    c = r.counters()
    assert (c["playing"], c["viewers"], c["sessions"], c["pieces"]) == (0, {}, 1, 1)  # totals only grow


def test_a_piece_that_failed_is_tried_again():
    part = "162136-698541"
    fake = FakePluto([media([(0, SHOW.format(part=part, n=0))], seq=99, key=KEY.format(part=part))])
    tries = []

    def flaky_remux(body, key, iv, dur, t0):
        tries.append(1)
        return b"" if len(tries) == 1 else b"TS" + body  # a network blip, then it works

    r = pluto.Relay(None, log=lambda m: None, fetcher=fake, remux_fn=flaky_remux)
    look(r)
    assert r.handle(f"/pluto/{CID}/0.ts", "192.0.2.30")[0] == 502
    status, _, body = r.handle(f"/pluto/{CID}/0.ts", "192.0.2.31")  # the next viewer, or the same one again
    assert status == 200 and body.startswith(b"TSpiece:") and len(tries) == 2
    r.handle(f"/pluto/{CID}/0.ts", "192.0.2.30")
    assert len(tries) == 2  # once it worked, it's shared


def test_the_relay_plays_the_channels_kias_playlist_has(tmp_path: Path, monkeypatch):
    # cli.pluto_relay finds them in curated.m3u by the address for_players gives them, so the
    # two have to agree, or every Pluto channel would be refused.
    from types import SimpleNamespace

    from kia import cli
    from kia.m3u import write

    monkeypatch.setattr(cli, "_RELAY", None)
    monkeypatch.setattr(pluto.Relay, "keep_status_fresh", lambda self: None)
    cfg = SimpleNamespace(pluto_relay=True, output_dir=tmp_path, state_dir=tmp_path, alerts=None)
    cid = "65c69bbfd77d450008c7ffee"
    entries = pluto.for_players([Entry("Pluto", f"https://jmp2.uk/plu-{cid}.m3u8", {}),
                                 Entry("Other", "https://example.com/other.m3u8", {})], "http://192.168.1.20:8780")  # fmt: skip
    relay = cli.pluto_relay(cfg)
    assert relay.allowed() == set()  # no playlist yet
    (tmp_path / "curated.m3u").write_text(write(entries, None))
    assert relay.allowed() == {cid}


def test_an_answer_too_big_is_refused_without_reading_it_all(monkeypatch):
    # The relay downloads from other people's servers; one huge answer must not fill memory.
    read = []

    class Huge:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://example.com/piece.ts"

        def read(self, n=-1):
            read.append(n)
            return b"x" * (n if n >= 0 else 10**9)

    monkeypatch.setattr(guard, "get", lambda: None)  # as with a proxy of your own: _OPENER alone
    monkeypatch.setattr(pluto._OPENER, "open", lambda req, timeout=None: Huge())
    with pytest.raises(pluto.TooBig):
        pluto.fetch("https://example.com/piece.ts")
    assert read == [pluto.MAX_DOWNLOAD + 1]  # asked for one byte over the limit, no more
    with pytest.raises(pluto.TooBig):  # and not asked again: it won't be smaller next time
        pluto.fetch_retry("https://example.com/piece.ts")


def test_pieces_of_channels_not_playing_dont_take_places(tmp_path: Path):
    fake = FakePluto([media([(0, AD.format(n=960, sig="a"))])])
    r, _ = relay(fake)
    ids = [f"{n:024x}" for n in range(pluto.MAX_CHANNELS)]
    for cid in ids:  # old piece addresses, or someone trying them
        status, _, _ = r.handle(f"/pluto/{cid}/0.ts", "192.0.2.9")
        assert status == 404
    assert r.channels == {}
    status, _, _ = r.handle(f"/pluto/{CID}.m3u8", "192.0.2.9")  # a real viewer still gets in
    assert status == 200


def test_a_short_session_is_renewed_near_its_end_not_at_every_clip(monkeypatch):
    fake = FakePluto([media([(n * 5, SHOW.format(part=f"p{n // 2}", n=n)) for n in range(k, k + 2)]) for k in range(0, 40, 2)])
    fake.refresh_in = 600  # ten minutes: "half an hour before" would be from the start
    r, _ = relay(fake)
    for _ in range(4):  # a new clip every second look
        look(r)
    ch = r.channel(CID)
    assert fake.boots == 1 and not ch.renew
    ch.session.started -= 500  # past three quarters of it: renewed at the next clip change
    look(r)
    look(r)
    assert fake.boots == 2 and not ch.renew


def test_a_session_too_old_is_replaced_even_without_a_clip_change():
    part = "p"
    fake = FakePluto([media([(n * 5, SHOW.format(part=part, n=n))]) for n in range(10)])
    r, _ = relay(fake)
    look(r)
    ch = r.channel(CID)
    ch.session.started -= ch.session.refresh_after * 1.6
    look(r)
    assert fake.boots == 2


def test_back_on_its_own_session_after_the_jmp2_link():
    fake = FakePluto([media([(n * 5, SHOW.format(part="p", n=n))]) for n in range(10)], boot_works=False)
    r, _ = relay(fake)
    look(r)
    ch = r.channel(CID)
    assert not ch.session.own
    fake.boot_works = True
    look(r)
    assert not ch.session.own  # not before FALLBACK_RETRY
    ch.last_session_try -= pluto.FALLBACK_RETRY + 1
    look(r)
    assert ch.session.own and ch.fallback_since is None


def test_a_long_fallback_sends_one_alert_an_hour():
    import threading

    fake = FakePluto([media([(n * 5, SHOW.format(part="p", n=n))]) for n in range(10)], boot_works=False)
    told, done = [], threading.Event()
    r = pluto.Relay(None, log=lambda m: None, fetcher=fake, remux_fn=lambda *a: b"TS",
                    on_fallback=lambda cid, minutes: (told.append((cid, minutes)), done.set()))  # fmt: skip
    assert r.handle(f"/pluto/{CID}.m3u8", "x")[0] == 200
    assert told == []  # just fell back
    r.channel(CID).fallback_since -= pluto.FALLBACK_ALERT + 60
    r.channel(CID).last_fetch = 0.0
    r.handle(f"/pluto/{CID}.m3u8", "x")
    assert done.wait(5) and told[0][0] == CID and told[0][1] >= pluto.FALLBACK_ALERT // 60
    sent = r.alerted[CID]
    r.handle(f"/pluto/{CID}.m3u8", "x")
    # Not again within the hour. The alert goes out on a thread of its own, so check the
    # decision itself, which is made at once: a second alert would have moved this time.
    assert r.alerted == {CID: sent} and len(told) == 1


def test_what_a_channel_remembers_stays_small():
    fake = FakePluto([media([(0, SHOW.format(part="p", n=0))])])
    r, _ = relay(fake)
    look(r)
    ch = r.channel(CID)
    for n in range(300):
        ch.piece(0, f"viewer-{n}")
        ch.viewers[f"viewer-{n}"] -= 400  # gone for a while
    assert len(ch.viewers) <= 51
    for n in range(300):
        ch.furthest[f"clip-{n}"] = 0
    ch._take([{"pdt": T0 + timedelta(seconds=10), "dur": 5.0, "uri": SHOW.format(part="new", n=1), "key_uri": None,
               "iv": None, "seq": 1, "disc": False}])  # fmt: skip
    assert len(ch.furthest) <= 201
